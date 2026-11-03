# SPDX-License-Identifier: Apache-2.0
"""Mémoire (``loom.memory``) : modèles, sans torch ni transformers.

Des modules ``torch`` et ``transformers`` factices les remplacent : ils
calculent sur des listes, assez pour éprouver ce que fait ``bge_m3`` autour
des modèles — tokens spéciaux écartés, poids lexical le plus fort gardé,
dense normé, sigmoïde du reranker — et ce que fait ``LazyModel`` : un seul
chargement sous concurrence, un modèle cassé jamais gardé, un calcul raté qui
garde le modèle, une dépendance absente qui nomme l'extra.
"""

import contextlib
import logging
import math
import sys
import threading
import time
import types
import warnings
from collections.abc import Generator, Iterator, Sequence
from typing import Any, cast

import pytest

from loom_ia.tools_builtin.memory.bge_m3 import BgeM3Embedder, BgeReranker
from loom_ia.tools_builtin.memory.models import (
    LazyModel,
    ModelLoadError,
    ModelRunError,
    resolve_device,
    shared_models,
    unsupported_gpu,
)
from loom_ia.tools_builtin.memory_params import ModelsSpec

CPU = ModelsSpec(device="cpu")

# --- torch et transformers factices -----------------------------------------------------


class Tensor:
    """Un tenseur réduit à des listes imbriquées, avec ce que ``bge_m3`` en utilise."""

    def __init__(self, data: Any) -> None:
        self.data = data

    def __getitem__(self, key: Any) -> Tensor:
        if isinstance(key, tuple):
            rows, column = cast(tuple[Any, Any], key)
            assert rows == slice(None)
            return Tensor([row[column] for row in self.data])
        return Tensor(self.data[key])

    def float(self) -> Tensor:
        return self

    def to(self, _device: str) -> Tensor:
        return self

    def tolist(self) -> Any:
        return self.data

    def squeeze(self, dim: int) -> Tensor:
        assert dim == -1

        def first(leaf: Any) -> Any:
            return leaf[0]

        return Tensor(_map(first, self.data, depth=_depth(self.data) - 1))

    def view(self, size: int) -> Tensor:
        assert size == -1
        return Tensor(list(_leaves(self.data)))


def _depth(data: Any) -> int:
    return 1 + _depth(data[0]) if isinstance(data, list) and data else 0


def _map(fn: Any, data: Any, *, depth: int) -> Any:
    if depth == 0:
        return fn(data)
    return [_map(fn, item, depth=depth - 1) for item in data]


def _leaves(data: Any) -> Iterator[float]:
    if isinstance(data, list):
        for item in cast(list[Any], data):
            yield from _leaves(item)
    else:
        yield data


class Module:
    def __init__(self, fakes: Fakes | None = None) -> None:
        self.fakes = fakes
        self.placed: list[str] = []
        self.broken = False

    def to(self, device: str) -> Module:
        self.placed.append(device)
        return self

    def half(self) -> Module:
        self.placed.append("half")
        return self

    def eval(self) -> Module:
        return self

    def _computing(self) -> None:
        if self.broken:
            raise NotImplementedError("Cannot copy out of meta tensor; no data!\nDétail")
        if self.fakes is not None:
            self.fakes.enter()


class Linear(Module):
    """La tête lexicale : poids lus dans ``sparse_linear.pt``."""

    def __init__(self, inputs: int, outputs: int) -> None:
        super().__init__()
        assert outputs == 1
        self.weight = [0.0] * inputs

    def load_state_dict(self, state: dict[str, list[float]]) -> None:
        self.weight = state["weight"]

    def __call__(self, hidden: Tensor) -> Tensor:
        def project(vector: list[float]) -> list[float]:
            return [math.fsum(w * x for w, x in zip(self.weight, vector, strict=True))]

        return Tensor(_map(project, hidden.data, depth=2))


SPECIAL = {"cls": 0, "pad": 1, "eos": 2, "unk": 3}


class Tokenizer:
    """Un mot = un identifiant (à partir de 10), entouré de cls et eos, complété par pad."""

    cls_token_id = SPECIAL["cls"]
    pad_token_id = SPECIAL["pad"]
    eos_token_id = SPECIAL["eos"]
    unk_token_id = SPECIAL["unk"]

    def __init__(self) -> None:
        self.vocabulary: dict[str, int] = {}

    def ids(self, text: str) -> list[int]:
        return [self.vocabulary.setdefault(w, 10 + len(self.vocabulary)) for w in text.split()]

    def __call__(self, texts: list[str], pairs: list[str] | None = None, **options: Any) -> Batch:
        assert options["return_tensors"] == "pt" and options["padding"] is True
        if pairs is None:
            rows = [[0, *self.ids(t), 2] for t in texts]
        else:
            assert options["truncation"] == "only_second"
            rows = [
                [0, *self.ids(q), 2, *self.ids(p), 2] for q, p in zip(texts, pairs, strict=True)
            ]
        width = max(len(row) for row in rows)
        return Batch({"input_ids": Tensor([row + [1] * (width - len(row)) for row in rows])})


class Batch(dict[str, Tensor]):
    def to(self, _device: str) -> Batch:
        return self


class Encoder(Module):
    """Le vecteur caché d'un token : [identifiant, 1] ; la tête lexicale vaut donc id - 0,5."""

    config = types.SimpleNamespace(hidden_size=2)

    def __call__(self, input_ids: Tensor, return_dict: bool) -> Any:
        assert return_dict
        self._computing()
        hidden = [[[float(token), 1.0] for token in row] for row in input_ids.data]
        return types.SimpleNamespace(last_hidden_state=Tensor(hidden))


class PairClassifier(Module):
    """Logit : nombre de mots de la question repris par le passage, moins 1."""

    def __call__(self, input_ids: Tensor, return_dict: bool) -> Any:
        assert return_dict
        self._computing()
        logits: list[list[float]] = []
        for row in input_ids.data:
            end = row.index(2)
            question = set(row[1:end])
            passage = {token for token in row[end + 1 :] if token >= 10}
            logits.append([float(len(question & passage) - 1)])
        return types.SimpleNamespace(logits=Tensor(logits))


class Fakes:
    """Les deux modules factices, et ce qu'on leur a demandé."""

    def __init__(self, *, slow: float = 0.0, broken: int = 0, cached: bool = True) -> None:
        self.built = 0
        self.broken = broken
        self.slow = slow
        self.inside = 0
        self.overlap = False
        self.lock = threading.Lock()
        self.files: list[tuple[str, str]] = []
        self.encoders: list[Encoder] = []
        # Le cache local de Hugging Face a-t-il les fichiers ? Sinon, hors ligne, OSError.
        self.cached = cached
        # Chaque lecture du Hub : ce qu'on a lu, et si c'était hors ligne.
        self.reads: list[tuple[str, bool]] = []
        self.bars_off = 0

    def _read(self, what: str, options: dict[str, Any]) -> None:
        offline = bool(options.pop("local_files_only", False))
        assert not options
        self.reads.append((what, offline))
        if offline and not self.cached:
            raise OSError(f"{what} absent du cache\nDétail")

    def enter(self) -> None:
        """Un chargement ou un calcul : deux en même temps se voient (``overlap``)."""
        with self.lock:
            self.inside += 1
            self.overlap = self.overlap or self.inside > 1
        time.sleep(self.slow)
        with self.lock:
            self.inside -= 1

    def _built[M: Module](self, model: M) -> M:
        self.enter()
        with self.lock:
            self.built += 1
            model.broken = self.built <= self.broken
        return model

    def torch(self) -> types.ModuleType:
        torch = types.ModuleType("torch")

        def normalize(t: Tensor, dim: int) -> Tensor:
            assert dim == -1
            rows: list[list[float]] = t.data
            return Tensor([[x / math.sqrt(sum(v * v for v in row)) for x in row] for row in rows])

        def relu(t: Tensor) -> Tensor:
            def positive(x: float) -> float:
                return max(x, 0.0)

            return Tensor(_map(positive, t.data, depth=_depth(t.data)))

        def sigmoid(t: Tensor) -> Tensor:
            values: list[float] = t.data
            return Tensor([1 / (1 + math.exp(-x)) for x in values])

        def load(path: str, map_location: str, weights_only: bool) -> dict[str, list[float]]:
            assert map_location == "cpu" and weights_only
            return {"weight": [1.0, -0.5]}

        torch.nn = types.SimpleNamespace(  # type: ignore[attr-defined]
            Linear=Linear, functional=types.SimpleNamespace(normalize=normalize)
        )
        torch.relu = relu  # type: ignore[attr-defined]
        torch.sigmoid = sigmoid  # type: ignore[attr-defined]
        torch.inference_mode = contextlib.nullcontext  # type: ignore[attr-defined]
        torch.load = load  # type: ignore[attr-defined]

        def capability(_index: int) -> tuple[int, int]:
            return (12, 0)

        def name(_index: int) -> str:
            return "NVIDIA GeForce RTX 5060 Ti"

        # Une carte récente, prise en charge : un ``cuda`` forcé est accepté.
        torch.__version__ = "2.15.0+cu130"  # type: ignore[attr-defined]
        torch.cuda = types.SimpleNamespace(  # type: ignore[attr-defined]
            is_available=lambda: True,
            device_count=lambda: 1,
            get_device_capability=capability,
            get_device_name=name,
            get_arch_list=lambda: ["sm_80", "sm_90", "sm_120"],
        )
        return torch

    def transformers(self) -> types.ModuleType:
        def tokenizer(name: str, **options: Any) -> Tokenizer:
            self._read("tokenizer", options)
            return Tokenizer()

        def encoder(name: str, **options: Any) -> Encoder:
            self._read("encoder", options)
            model = self._built(Encoder(self))
            self.encoders.append(model)
            return model

        def classifier(name: str, **options: Any) -> PairClassifier:
            self._read("classifier", options)
            return self._built(PairClassifier(self))

        def cached(name: str, filename: str, **options: Any) -> str:
            self._read(filename, options)
            self.files.append((name, filename))
            return f"/cache/{name}/{filename}"

        def bars_off() -> None:
            self.bars_off += 1

        module = types.ModuleType("transformers")
        module.AutoTokenizer = types.SimpleNamespace(from_pretrained=tokenizer)  # type: ignore[attr-defined]
        module.AutoModel = types.SimpleNamespace(from_pretrained=encoder)  # type: ignore[attr-defined]
        module.AutoModelForSequenceClassification = types.SimpleNamespace(  # type: ignore[attr-defined]
            from_pretrained=classifier
        )
        module.utils = types.SimpleNamespace(  # type: ignore[attr-defined]
            cached_file=cached, logging=types.SimpleNamespace(disable_progress_bar=bars_off)
        )
        return module


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> Fakes:
    found = Fakes()
    monkeypatch.setitem(sys.modules, "torch", found.torch())
    monkeypatch.setitem(sys.modules, "transformers", found.transformers())
    return found


def install(monkeypatch: pytest.MonkeyPatch, found: Fakes) -> Fakes:
    monkeypatch.setitem(sys.modules, "torch", found.torch())
    monkeypatch.setitem(sys.modules, "transformers", found.transformers())
    return found


# --- BGE-M3 -----------------------------------------------------------------------------


def test_bge_m3_dense_is_the_normed_first_token(fakes: Fakes) -> None:
    embedder = BgeM3Embedder(CPU)
    [embedding] = embedder.embed_documents(["devis chauffe-eau"])
    # [CLS] = identifiant 0 : son vecteur caché [0, 1] est déjà normé.
    assert embedding.dense == [0.0, 1.0]
    assert embedder.dim == 1024 and embedder.model_name == "BAAI/bge-m3"
    assert fakes.files == [("BAAI/bge-m3", "sparse_linear.pt")]


def test_bge_m3_lexical_skips_special_tokens_and_keeps_the_strongest(fakes: Fakes) -> None:
    embedder = BgeM3Embedder(CPU)
    short, longer = embedder.embed_documents(["devis", "chauffe-eau devis devis"])
    # Poids d'un token : identifiant - 0,5 ; cls, eos et pad (0, 2, 1) sont écartés.
    # L'identifiant 10 est celui de « warmup », le texte du premier passage au chargement.
    devis, chauffe_eau = 11, 12
    assert short.sparse.indices == [devis]
    assert short.sparse.values == [devis - 0.5]
    assert longer.sparse.indices == [devis, chauffe_eau]
    assert longer.sparse.values == [devis - 0.5, chauffe_eau - 0.5]


def test_bge_m3_works_by_batches_of_sixteen(fakes: Fakes) -> None:
    embedder = BgeM3Embedder(CPU)
    texts = [f"mot{i}" for i in range(40)]
    found = embedder.embed_documents(texts)
    assert len(found) == 40
    assert found[39].sparse.indices == [11 + 39]
    assert embedder.embed_documents([]) == []


def test_fp16_only_on_gpu(fakes: Fakes) -> None:
    with fresh_devices():
        BgeM3Embedder(ModelsSpec(device="cuda")).embed_query("x")
        BgeM3Embedder(ModelsSpec(device="cpu")).embed_query("x")
    on_gpu, on_cpu = (encoder.placed for encoder in fakes.encoders)
    # Demi-précision d'abord : la carte ne reçoit pas les poids en float32.
    assert on_gpu == ["half", "cuda"]
    assert on_cpu == ["cpu"]


def test_the_reranker_scores_pairs_through_a_sigmoid(fakes: Fakes) -> None:
    reranker = BgeReranker(CPU)
    scores = reranker.score("devis chauffe-eau", ["devis chauffe-eau neuf", "rien", "devis"])
    assert scores == pytest.approx([1 / (1 + math.exp(-1)), 1 / (1 + math.exp(1)), 0.5])
    assert reranker.score("q", []) == []


def test_loading_silences_the_progress_bars(fakes: Fakes) -> None:
    BgeM3Embedder(CPU).embed_query("x")
    BgeReranker(CPU).score("q", ["p"])
    assert fakes.bars_off == 2


def test_by_default_the_hub_may_be_asked(fakes: Fakes) -> None:
    BgeM3Embedder(CPU).embed_query("x")
    BgeReranker(CPU).score("q", ["p"])
    assert fakes.reads == [
        ("tokenizer", False),
        ("encoder", False),
        ("sparse_linear.pt", False),
        ("tokenizer", False),
        ("classifier", False),
    ]


def test_offline_reads_every_file_from_the_local_cache(fakes: Fakes) -> None:
    offline = ModelsSpec(device="cpu", offline=True)
    BgeM3Embedder(offline).embed_query("x")
    BgeReranker(offline).score("q", ["p"])
    assert [what for what, _ in fakes.reads] == [
        "tokenizer",
        "encoder",
        "sparse_linear.pt",
        "tokenizer",
        "classifier",
    ]
    assert all(local for _, local in fakes.reads)


def test_offline_a_model_missing_from_the_cache_says_how_to_get_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    found = install(monkeypatch, Fakes(cached=False))
    embedder = BgeM3Embedder(ModelsSpec(device="cpu", offline=True))
    with pytest.raises(ModelLoadError) as caught:
        embedder.embed_query("x")
    assert str(caught.value) == (
        "Modèle BAAI/bge-m3 absent du cache local de Hugging Face (models.offline) : "
        "le télécharger avec « hf download BAAI/bge-m3 », ou retirer models.offline"
    )
    with pytest.raises(ModelLoadError, match="hf download BAAI/bge-reranker-v2-m3"):
        BgeReranker(ModelsSpec(device="cpu", offline=True)).score("q", ["p"])
    # Rien n'est gardé : le fichier mis au cache, l'appel suivant charge.
    found.cached = True
    assert embedder.embed_query("x").dense == [0.0, 1.0]


def test_online_a_hub_error_keeps_the_general_message(monkeypatch: pytest.MonkeyPatch) -> None:
    found = install(monkeypatch, Fakes())

    def unreachable(name: str, **options: Any) -> Tokenizer:
        raise OSError("We couldn't connect to 'https://huggingface.co'\nDétail")

    sys.modules["transformers"].AutoTokenizer = types.SimpleNamespace(  # type: ignore[attr-defined]
        from_pretrained=unreachable
    )
    with pytest.raises(ModelLoadError) as caught:
        BgeM3Embedder(CPU).embed_query("x")
    assert str(caught.value).startswith(
        "Chargement du modèle BAAI/bge-m3 impossible sur cpu : We couldn't connect"
    )
    assert found.built == 0


# --- LazyModel ------------------------------------------------------------------------


def test_two_simultaneous_first_calls_build_the_model_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    found = install(monkeypatch, Fakes(slow=0.1))
    embedder = BgeM3Embedder(CPU)
    threads = [threading.Thread(target=embedder.embed_query, args=("x",)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert found.built == 1
    assert not found.overlap  # ni deux chargements, ni deux calculs en même temps


def test_a_model_that_does_not_run_is_not_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    found = install(monkeypatch, Fakes(broken=1))
    embedder = BgeM3Embedder(CPU)
    with pytest.raises(ModelLoadError) as caught:
        embedder.embed_query("x")
    assert str(caught.value) == (
        "Chargement du modèle BAAI/bge-m3 impossible sur cpu : Cannot copy out of meta tensor; "
        "no data!. Réessayer ; si l'erreur persiste, vérifier models.device et "
        "l'installation de torch."
    )
    # L'appel suivant recharge : le modèle cassé n'a pas été gardé.
    assert embedder.embed_query("x").dense == [0.0, 1.0]
    assert found.built == 2


def test_the_reranker_is_guarded_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, Fakes(broken=1))
    reranker = BgeReranker(CPU)
    with pytest.raises(ModelLoadError, match="bge-reranker-v2-m3 impossible sur cpu"):
        reranker.score("q", ["p"])
    assert len(reranker.score("q", ["p", "r"])) == 2


def test_a_missing_dependency_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(ModelLoadError) as caught:
        BgeM3Embedder(CPU).embed_query("x")
    assert str(caught.value) == (
        "Modèle BAAI/bge-m3 : dépendance 'torch' absente (installer l'extra : loom-ia[memory])"
    )


def test_a_failed_computation_keeps_the_model() -> None:
    builds: list[str] = []
    failing = [False]

    def run(model: str) -> str:
        if failing[0]:
            raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB\nplus de détails")
        return model

    lazy = LazyModel[str]("BAAI/bge-m3", lambda: "cpu", lambda d: builds.append(d) or "m", run)
    assert lazy.run(run) == "m"
    failing[0] = True
    with pytest.raises(ModelRunError) as caught:
        lazy.run(run)
    assert str(caught.value) == (
        "Calcul du modèle BAAI/bge-m3 impossible : CUDA out of memory. Tried to allocate "
        "2.00 GiB. Réessayer ; si l'erreur persiste, relancer loom."
    )
    assert builds == ["cpu"]  # le modèle n'est pas jeté


# --- Registre et préchargement ----------------------------------------------------------


def test_the_same_settings_share_one_pair_of_models(fakes: Fakes) -> None:
    first = shared_models(ModelsSpec(device="cpu", preload=False))
    again = shared_models(ModelsSpec(device="cpu", preload=True))
    other = shared_models(ModelsSpec(device="cuda"))
    offline = shared_models(ModelsSpec(device="cpu", offline=True))
    assert first is again
    assert other is not first
    # Hors ligne, le chargement diffère : pas les mêmes modèles.
    assert offline is not first
    # Les factices ne sont pas partagés : ils ne coûtent rien.
    assert shared_models(ModelsSpec(kind="fake")) is not shared_models(ModelsSpec(kind="fake"))


def test_preload_loads_both_models_in_the_background(monkeypatch: pytest.MonkeyPatch) -> None:
    found = install(monkeypatch, Fakes())
    models = shared_models(ModelsSpec(device="cpu", embedder="pre/charge"))
    models.preload()
    models.preload()  # une seule fois
    thread = models._preloading  # pyright: ignore[reportPrivateUsage]
    assert thread is not None
    thread.join(timeout=5)
    assert found.built == 2


def test_a_failed_preload_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)
    models = shared_models(ModelsSpec(device="cpu", embedder="pre/absent"))
    with caplog.at_level(logging.ERROR, logger="loom_ia.memory"):
        models.preload()
        thread = models._preloading  # pyright: ignore[reportPrivateUsage]
        assert thread is not None
        thread.join(timeout=5)
    assert any("préchargement impossible" in r.getMessage() for r in caplog.records)


# --- Device -----------------------------------------------------------------------------


def _torch(
    capability: tuple[int, int], archs: list[str], *, cuda: bool = True, cards: int = 1
) -> Any:
    def name_of(_: int) -> str:
        return "NVIDIA GeForce GTX 1050 Ti"

    def capability_of(_: int) -> tuple[int, int]:
        warnings.warn("bruit de torch", UserWarning, stacklevel=1)
        return capability

    devices = types.SimpleNamespace(
        is_available=lambda: cuda,
        device_count=lambda: cards if cuda else 0,
        get_device_capability=capability_of,
        get_device_name=name_of,
        get_arch_list=lambda: archs,
    )
    mps = types.SimpleNamespace(is_available=lambda: False)
    return types.SimpleNamespace(
        __version__="2.14.0+cu130", cuda=devices, backends=types.SimpleNamespace(mps=mps)
    )


CU130 = ["sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"]


@contextlib.contextmanager
def fresh_devices() -> Generator[None]:
    resolve_device.cache_clear()
    try:
        yield
    finally:
        resolve_device.cache_clear()


def test_an_old_gpu_is_left_aside_with_one_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, "torch", _torch((6, 1), CU130))
    with fresh_devices(), warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        assert resolve_device("auto") == "cpu"
        assert resolve_device("auto") == "cpu"
    assert not seen
    messages = [r.getMessage() for r in caplog.records if r.name == "loom_ia.memory"]
    assert messages == [
        "GPU NVIDIA GeForce GTX 1050 Ti (sm_61) non pris en charge par ce torch (2.14.0+cu130, "
        "architectures : sm_75, sm_80, sm_86, sm_90, sm_100, sm_120) : modèles de la mémoire "
        "sur cpu (models.device pour forcer)"
    ]


@pytest.mark.parametrize(
    ("capability", "archs", "usable"),
    [
        ((6, 1), CU130, False),  # GTX 1050 Ti face à la roue cu130
        ((7, 5), CU130, True),
        ((7, 0), ["sm_75", "sm_80"], False),  # même majeure, mineure trop basse
        ((8, 9), ["sm_80", "sm_86", "sm_90"], True),  # 8.9 : binaire sm_86 de même majeure
        ((12, 0), ["sm_80", "compute_90"], True),  # PTX compilé à la volée
        ((6, 1), [], True),  # rien à juger : la carte est gardée
    ],
)
def test_gpu_support_follows_the_torch_rule(
    capability: tuple[int, int], archs: Sequence[str], usable: bool
) -> None:
    assert (unsupported_gpu(_torch(capability, list(archs))) is None) is usable


def test_a_supported_gpu_is_used_and_an_explicit_device_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "torch", _torch((12, 0), CU130, cards=2))
    with fresh_devices():
        assert resolve_device("auto") == "cuda"
        assert resolve_device("cuda") == "cuda"
        assert resolve_device("cuda:1") == "cuda:1"
        assert resolve_device("cpu") == "cpu"


@pytest.mark.parametrize(
    ("torch", "device", "said"),
    [
        (
            _torch((6, 1), CU130),
            "cuda",
            "models.device cuda inutilisable : GPU NVIDIA GeForce GTX 1050 Ti (sm_61) non pris en "
            "charge par ce torch (2.14.0+cu130, architectures : sm_75, sm_80, sm_86, sm_90, "
            "sm_100, sm_120)",
        ),
        (
            _torch((12, 0), CU130, cuda=False),
            "cuda",
            "models.device cuda inutilisable : aucun GPU CUDA vu par ce torch (2.14.0+cu130)",
        ),
        (
            _torch((12, 0), CU130),
            "cuda:2",
            "models.device cuda:2 inutilisable : pas de carte 2, torch en voit 1",
        ),
    ],
)
def test_a_forced_gpu_that_cannot_serve_is_refused_at_load(
    monkeypatch: pytest.MonkeyPatch, torch: Any, device: str, said: str
) -> None:
    """Forcé, ``cuda`` est vérifié comme ``auto`` : l'erreur dit pourquoi, avant tout calcul."""
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", Fakes().transformers())
    with fresh_devices(), pytest.raises(ModelLoadError) as caught:
        BgeM3Embedder(ModelsSpec(device=device)).embed_query("x")
    assert str(caught.value) == (
        f"Modèle BAAI/bge-m3 : {said}. Mettre models.device à auto (qui se replie sur cpu "
        "quand la carte ne convient pas) ou à cpu"
    )
