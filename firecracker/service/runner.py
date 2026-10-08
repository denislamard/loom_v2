"""runner — execute UN job, dans son propre processus.

Programme autonome, stdlib pure. Il n'importe rien de l'agent hote : le
framework d'orchestration porte la cle d'API et n'entre jamais dans la VM.

CONTRAT

    python3 runner.py --job /data/jobs/<sid>/job.json

    lit    job.json     entrypoint, args, chemins, limites resolues
    ecrit  result.json  {"ok": true, "result": ...} ou {"ok": false, "error": ...}
    sort   0            result.json est exploitable, y compris si le tool a
                        echoue (erreur METIER)
           != 0         defaillance de runner lui-meme

Le code de sortie ne dit PAS si le tool a reussi. Confondre les deux ferait
remonter toute exception metier en `runner_failure`, et le modele qui a ecrit
le tool perdrait le diagnostic dont il a besoin pour se corriger.

POURQUOI runner APPLIQUE SES PROPRES rlimit

La spec envisageait de les poser cote execd, dans le `preexec_fn` du fork.
Executer du code Python dans un enfant fraichement forke est le danger
classique (verrous d'allocateur herites d'un processus multi-thread), et
CPython documente `preexec_fn` comme non sur pour cette raison. Ici runner est
du code de confiance — root:root 0644, non modifiable par le compte sandbox —
et il pose ses limites AVANT d'importer la moindre ligne de code recu. Le
resultat est identique et le chemin est sur. Effet de bord utile : runner se
teste hors VM par un simple appel avec un job.json.
"""

from __future__ import annotations

# --- Imports : TOUS ici, avant que le repertoire de code ne soit ajoute a
# sys.path. Un tool qui embarquerait un `json.py` ne doit pas pouvoir occulter
# les modules dont runner a besoin pour rapporter l'erreur.
import argparse
import asyncio
import errno
import importlib
import importlib.util
import inspect
import json
import math
import os
import resource
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

# Modules dont les frames sont retirees des tracebacks (cf. clean_traceback).
_INTERNAL_MODULES = ("importlib", "runpy", "asyncio/runners.py", "asyncio/base_events.py")


# --------------------------------------------------------------------------- #
# Limites de ressources
# --------------------------------------------------------------------------- #
def apply_limits(limits: dict[str, int]) -> None:
    """Pose les rlimit du processus courant.

    On ne fait que BAISSER : abaisser une limite souple est permis a tout
    utilisateur, donc l'ordre vis-a-vis du changement d'uid (fait par execd
    avant l'exec) est sans importance.

    RLIMIT_AS EN DERNIER. Une fois l'espace d'adressage plafonne, toute
    allocation ulterieure peut echouer ; poser les autres limites d'abord
    evite qu'un echec du plafonnement lui-meme devienne illisible.
    """
    # Pas de core dump : sur un segfault d'extension C, il remplirait /data
    # avec un fichier de la taille de l'espace d'adressage, sans que personne
    # ne le lise jamais.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    # RLIMIT_CPU s'exprime en secondes entieres. On arrondit au superieur :
    # arrondir a l'inferieur tuerait un job juste sous sa limite annoncee.
    #
    # Limite SOUPLE strictement inferieure a la DURE, volontairement. Avec les
    # deux egales, le noyau passe directement au SIGKILL et execd ne voit qu'un
    # code de sortie -9, indiscernable du tueur OOM. Avec un ecart d'une
    # seconde, le depassement se manifeste par SIGXCPU : le diagnostic devient
    # exact au lieu d'etre devine.
    cpu_s = max(1, math.ceil(limits["cpu_ms"] / 1000))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))

    resource.setrlimit(resource.RLIMIT_FSIZE, (limits["fsize_bytes"],) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits["nofile"],) * 2)

    # RLIMIT_NPROC compte les processus du VRAI uid. Il borne le fork bomb ;
    # il n'est pas une limite par job, ce qui n'a pas d'importance ici : une
    # VM n'execute qu'un job a la fois par defaut.
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (limits["nproc"],) * 2)
    except (ValueError, OSError):
        # Absent ou refuse selon la plateforme : ne doit pas faire echouer le
        # job, les autres limites restent en place.
        pass

    resource.setrlimit(resource.RLIMIT_AS, (limits["mem_bytes"],) * 2)


# --------------------------------------------------------------------------- #
# Erreurs
# --------------------------------------------------------------------------- #
def error(kind: str, message: str, **detail: Any) -> dict[str, Any]:
    err: dict[str, Any] = {"kind": kind, "message": message}
    if detail:
        err["detail"] = detail
    return {"ok": False, "error": err}


def user_frames(exc: BaseException) -> list[traceback.FrameSummary]:
    """Frames appartenant au code recu, a l'exclusion de runner et de la stdlib.

    Sert deux fois : a nettoyer les tracebacks, et a decider si une exception
    provient de l'APPEL ou du CORPS de la fonction (voir run_job).
    """
    here = os.path.abspath(__file__)
    keep = []
    for frame in traceback.extract_tb(exc.__traceback__):
        filename = frame.filename or ""
        if os.path.abspath(filename) == here:
            continue
        if any(marker in filename for marker in _INTERNAL_MODULES):
            continue
        keep.append(frame)
    return keep


def clean_traceback(exc: BaseException, code_dir: Path) -> list[str]:
    """Traceback reduit aux frames du code de l'utilisateur.

    Un modele qui voit `/opt/execd/runner.py` ou `importlib/_bootstrap.py`
    dans sa pile part corriger le mauvais fichier. On ne garde que ce qu'il
    peut effectivement modifier, avec des chemins relatifs a code/ pour qu'ils
    correspondent aux noms qu'il a lui-meme choisis.
    """
    out: list[str] = []

    for frame in user_frames(exc):
        filename = frame.filename or ""
        try:
            shown = str(Path(filename).resolve().relative_to(code_dir.resolve()))
        except (ValueError, OSError):
            shown = filename

        line = f'  File "{shown}", line {frame.lineno}, in {frame.name}'
        if frame.line:
            line += f"\n    {frame.line}"
        out.append(line)

    out.append(f"{type(exc).__name__}: {exc}")
    return out


# --------------------------------------------------------------------------- #
# Resolution de l'entrypoint
# --------------------------------------------------------------------------- #
def load_entrypoint(entrypoint: str, code_dir: Path):
    """Resout « module.chemin:fonction » en objet appelable.

    Distingue deux echecs que le message brut de Python confond :
      - le MODULE DU TOOL est introuvable      -> code_invalid
      - une DEPENDANCE du tool est introuvable -> import_error

    La difference compte : le premier signale une erreur de packaging cote
    hote, le second alimente l'arbitrage socle / vendoring / rejet.
    """
    if ":" not in entrypoint:
        raise ExecError(
            "code_invalid",
            "entrypoint mal forme : « module:fonction » attendu",
            entrypoint=entrypoint,
        )

    module_name, _, func_name = entrypoint.partition(":")
    module_name, func_name = module_name.strip(), func_name.strip()
    if not module_name or not func_name:
        raise ExecError("code_invalid", "entrypoint incomplet", entrypoint=entrypoint)

    # Le repertoire de code entre dans sys.path MAINTENANT, apres tous les
    # imports de runner (voir l'en-tete du fichier).
    sys.path.insert(0, str(code_dir))

    # find_spec avant import : c'est ce qui permet de dire « ton module n'est
    # pas la » plutot que « un module n'est pas la ».
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        available = sorted(
            p.stem for p in code_dir.glob("*.py") if not p.name.startswith("_")
        ) + sorted(
            p.name for p in code_dir.iterdir() if p.is_dir() and (p / "__init__.py").exists()
        )
        raise ExecError(
            "code_invalid",
            f"module introuvable dans code/ : {module_name!r}",
            entrypoint=entrypoint,
            available=available[:20],
        )

    try:
        module = importlib.import_module(module_name)
    except SyntaxError as exc:
        raise ExecError(
            "code_invalid",
            f"erreur de syntaxe : {exc.msg}",
            file=os.path.basename(exc.filename or ""),
            line=exc.lineno,
        ) from exc
    except ModuleNotFoundError as exc:
        raise ExecError(
            "import_error",
            f"dependance absente : {exc.name!r}",
            module=exc.name,
        ) from exc
    except ImportError as exc:
        raise ExecError("import_error", str(exc), module=getattr(exc, "name", None)) from exc

    func = getattr(module, func_name, None)
    if func is None:
        exported = sorted(
            n for n in vars(module) if not n.startswith("_") and callable(vars(module)[n])
        )
        raise ExecError(
            "code_invalid",
            f"fonction {func_name!r} absente du module {module_name!r}",
            entrypoint=entrypoint,
            available=exported[:20],
        )
    if not callable(func):
        raise ExecError("code_invalid", f"{entrypoint!r} n'est pas appelable")

    return func


class ExecError(Exception):
    """Echec attribuable au job, avec un kind deja determine."""

    def __init__(self, kind: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.payload = error(kind, message, **detail)


# --------------------------------------------------------------------------- #
# Appel
# --------------------------------------------------------------------------- #
def call(func, args: dict[str, Any]) -> Any:
    """Appelle le tool, en gerant les fonctions asynchrones.

    On n'ouvre une boucle asyncio QUE si c'est necessaire : un tool synchrone
    ne doit pas payer le cout d'un event loop, et l'absence de boucle rend le
    diagnostic plus simple quand quelque chose deraille.
    """
    if inspect.iscoroutinefunction(func):
        return asyncio.run(func(**args))

    result = func(**args)
    if inspect.isawaitable(result):
        # Cas rare : fonction synchrone qui RENVOIE un awaitable.
        async def _await(aw):
            return await aw

        return asyncio.run(_await(result))
    return result


def serialize(result: Any) -> Any:
    """Verifie que le resultat est du JSON valide.

    Strict par choix : `allow_nan=False` refuse NaN et Infinity, qui ne font
    pas partie de JSON et que la plupart des parseurs cote client rejettent.
    Aucun repli sur repr() — il masquerait le defaut et donnerait au modele un
    resultat qui a l'air correct.
    """
    try:
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ExecError(
            "bad_result",
            f"valeur de retour non serialisable en JSON : {exc}",
            type=type(result).__name__,
        ) from exc
    return result


# --------------------------------------------------------------------------- #
# Ecriture du resultat
# --------------------------------------------------------------------------- #
def write_result(path: Path, payload: dict[str, Any]) -> None:
    """Ecriture atomique : temporaire unique, fsync, os.replace.

    execd lit ce fichier des que le processus se termine. Sans atomicite, il
    pourrait tomber sur un fichier tronque et rapporter une defaillance de
    runner alors que le job s'est bien passe.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".result-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# Entree
# --------------------------------------------------------------------------- #
def run_job(job: dict[str, Any]) -> dict[str, Any]:
    dirs = job["dirs"]
    code_dir = Path(dirs["code"])

    os.environ.update(
        {
            "CODE_DIR": dirs["code"],
            "IN_DIR": dirs["in"],
            "OUT_DIR": dirs["out"],
            "WORK_DIR": dirs["work"],
            "HOME": dirs["work"],
            "TMPDIR": dirs["work"],
        }
    )
    os.chdir(dirs["work"])

    args = job.get("args") or {}
    if not isinstance(args, dict):
        return error("code_invalid", "args : objet JSON attendu (arguments nommes)")

    try:
        func = load_entrypoint(job["entrypoint"], code_dir)
    except ExecError as exc:
        return exc.payload

    try:
        result = call(func, args)
    except ExecError as exc:
        return exc.payload
    except MemoryError:
        # RLIMIT_AS atteint proprement : Python leve MemoryError avant que le
        # noyau ne tue le processus. Le distinguer d'une exception metier
        # oriente vers la bonne correction (limite, pas code).
        return error("oom", "memoire epuisee", mem_bytes=job["limits"]["mem_bytes"])
    except TypeError as exc:
        # Signature incompatible : le LLM a produit des arguments qui ne
        # correspondent pas a la fonction. C'est code_invalid, pas une erreur
        # metier — mais seulement si l'exception vient de l'APPEL et non du
        # CORPS. Le critere est l'absence de toute frame utilisateur : si
        # l'execution n'est jamais entree dans le code recu, c'est l'appel qui
        # a echoue. Compter les frames brutes ne marche pas, l'appel en
        # traverse plusieurs dans runner lui-meme.
        if not user_frames(exc):
            try:
                signature = str(inspect.signature(func))
            except (TypeError, ValueError):
                signature = "?"
            return error(
                "code_invalid",
                f"arguments incompatibles avec la signature : {exc}",
                expected=f"{job['entrypoint'].split(':')[-1]}{signature}",
                got=sorted(args),
            )
        return error(
            "tool_raised", str(exc), type="TypeError", traceback=clean_traceback(exc, code_dir)
        )
    except ModuleNotFoundError as exc:
        # Import TARDIF, ecrit dans le corps de la fonction. Le modele produit
        # frequemment ce style. Le classer en tool_raised priverait l'hote du
        # nom de module isole, qui est precisement ce dont il a besoin pour
        # decider : socle, vendoring, ou rejet.
        return error(
            "import_error",
            f"dependance absente : {exc.name!r}",
            module=exc.name,
            traceback=clean_traceback(exc, code_dir),
        )
    except OSError as exc:
        # EFBIG = RLIMIT_FSIZE atteint. Le remonter en tool_raised enverrait le
        # modele corriger son code, alors que le probleme est la limite. EDQUOT
        # et ENOSPC relevent du meme diagnostic : le volume, pas la logique.
        if exc.errno in (errno.EFBIG, errno.ENOSPC, errno.EDQUOT):
            return error(
                "limit_exceeded",
                f"ecriture refusee : {exc.strerror}",
                limit="fsize_bytes",
                ceiling=job["limits"]["fsize_bytes"],
                traceback=clean_traceback(exc, code_dir),
            )
        return error(
            "tool_raised",
            str(exc),
            type=type(exc).__name__,
            traceback=clean_traceback(exc, code_dir),
        )
    except SystemExit as exc:
        return error("tool_raised", f"le tool a appele sys.exit({exc.code})", type="SystemExit")
    except Exception as exc:
        return error(
            "tool_raised",
            str(exc) or type(exc).__name__,
            type=type(exc).__name__,
            traceback=clean_traceback(exc, code_dir),
        )

    try:
        return {"ok": True, "result": serialize(result)}
    except ExecError as exc:
        return exc.payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute un job execd.")
    parser.add_argument("--job", required=True, help="chemin du descripteur job.json")
    opts = parser.parse_args()

    try:
        job = json.loads(Path(opts.job).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Rien a ecrire dans result.json : on ne sait meme pas ou il va.
        print(f"runner: job.json illisible : {exc}", file=sys.stderr)
        return 2

    result_path = Path(job["result_path"])

    try:
        apply_limits(job["limits"])
    except (OSError, ValueError, KeyError) as exc:
        payload = error("runner_failure", f"application des limites impossible : {exc}")
    else:
        payload = run_job(job)

    try:
        write_result(result_path, payload)
    except OSError as exc:
        print(f"runner: ecriture de result.json impossible : {exc}", file=sys.stderr)
        return 3

    # 0 meme quand le tool a echoue : result.json est exploitable, c'est le
    # seul sens de ce code de sortie.
    return 0


if __name__ == "__main__":
    sys.exit(main())
