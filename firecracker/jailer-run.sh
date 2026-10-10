#!/usr/bin/env bash
#
# ============================================================================
# jailer-run.sh — lance la microVM sous jailer
# ============================================================================
#
# CE QUE FAIT LE JAILER
#
# Il s'intercale entre toi et firecracker : il prépare un environnement
# confiné, abandonne les privilèges, puis exec() dans firecracker. Le VMM
# n'est donc jamais lancé en root.
#
# Enchaînement exact (doc officielle) :
#   1. ferme tous les descripteurs hérités, purge les variables d'environnement
#   2. crée <base>/<nom_exec>/<id>/root et y COPIE le binaire firecracker
#      (copie, pas lien : évite tout partage mémoire entre VMs)
#   3. applique les setrlimit demandés (par défaut no-file=2048)
#   4. crée les cgroups et y inscrit le pid
#   5. unshare(mount ns) + pivot_root() + chroot dans le répertoire
#   6. mknod de /dev/kvm et /dev/net/tun DANS la jail
#   7. chown du répertoire racine et des deux devices vers uid:gid
#   8. setns() sur le netns si --netns
#   9. clone(CLONE_NEWPID) si --new-pid-ns
#  10. setuid/setgid vers uid:gid, puis exec firecracker
#
# CONSÉQUENCE PRINCIPALE
#
# Après le pivot_root, firecracker ne voit plus que le contenu de <chroot>.
# Tous les chemins de vm-config.json doivent donc être exprimés RELATIVEMENT
# à cette racine, et les fichiers doivent y être physiquement présents et
# appartenir à uid:gid.
#
#   hôte   : /srv/jailer/firecracker/<id>/root/rootfs.ext4
#   config : "path_on_host": "rootfs.ext4"
#
# CE QUE ÇA PROTÈGE
#
# L'hôte, pas l'invité. Root dans la VM reste root dans la VM. Le jailer
# limite ce qu'un firecracker compromis pourrait atteindre sur la machine :
# système de fichiers réduit au strict nécessaire, privilèges abandonnés,
# ressources plafonnées, PID namespace séparé. C'est complémentaire du filtre
# seccomp que firecracker applique lui-même à ses threads.
#
# PRÉ-REQUIS
#
# jailer doit être lancé en root, et son binaire doit provenir de la même
# release que firecracker (builds musl statiques des releases officielles).
#
# USAGE
#
#   sudo ./jailer-run.sh                    # console série interactive
#   sudo VM_ID=agent-01 ./jailer-run.sh     # jail nommée
#   sudo LINK=0 ./jailer-run.sh             # copie du rootfs au lieu du lien
#   sudo INTERACTIVE=0 ./jailer-run.sh      # détaché, pilotage par l'API
#
# ARRÊT
#
#   `reboot` depuis le guest       arrêt ordonné (reboot=k termine le process)
#   Ctrl-C                          brutal, suffisant en développement
#   SendCtrlAltDel via l'API        équivalent bouton d'alimentation
#
# ============================================================================

set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

# ----------------------------------------------------------------------------
# Paramètres
# ----------------------------------------------------------------------------

# Identifiant de la VM : alphanumérique et tirets, 64 caractères max.
#
# Valeur fixe par défaut, volontairement : avec un id horodaté, chaque
# lancement crée une jail de plusieurs Go que le jailer ne supprime jamais.
# Un id stable fait réutiliser (et nettoyer) le même répertoire.
VM_ID="${VM_ID:-dev}"
CHROOT_BASE="${CHROOT_BASE:-/srv/jailer}"

# Compte non privilégié sous lequel tournera firecracker.
#
# On lit SUDO_UID/SUDO_GID, pas `id -u` : le script s'exécute sous sudo, donc
# `id -u` renverrait 0 et firecracker tournerait en root dans la jail — on
# garderait le chroot et les cgroups, mais on perdrait l'abandon de
# privilèges, qui est l'essentiel de l'intérêt du jailer.
#
# Le repli sur 1000 couvre le cas d'un shell root sans sudo.
JAIL_UID="${JAIL_UID:-${SUDO_UID:-1000}}"
JAIL_GID="${JAIL_GID:-${SUDO_GID:-1000}}"

# Console interactive. À 1, le jailer exec() directement dans firecracker, qui
# hérite de stdin/stdout : le boot s'affiche et le shell du guest est
# utilisable. À 0, on ajoute --new-pid-ns et --daemonize : firecracker est
# isolé dans son propre espace de PID et détaché du terminal, ce qui est le
# mode voulu pour de l'orchestration par l'API, mais rend la console
# inaccessible.
#
# Les deux sont incompatibles : --new-pid-ns fait passer par clone(), donc le
# processus qui porte firecracker n'est plus celui attaché à ton terminal.
INTERACTIVE="${INTERACTIVE:-1}"

# Plafonds cgroup v2. cpu.max "<quota> <période>" en microsecondes :
# 200000/100000 = 2 vCPU pleins.
CPU_MAX="${CPU_MAX:-200000 100000}"
MEM_MAX="${MEM_MAX:-1610612736}"        # 1,5 GiB : RAM invitée + surcoût VMM

# Le répertoire racine de la jail, tel que vu depuis l'hôte.
CHROOT_DIR="${CHROOT_BASE}/firecracker/${VM_ID}/root"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# VM_ID nomme un dossier que ce script efface en root (rm -rf) : ni « .. », ni « / »,
# ni rien d'autre que ce que le jailer accepte pour un id. À contrôler avant tout le reste.
[[ $VM_ID =~ ^[A-Za-z0-9-]{1,64}$ ]] \
    || die "VM_ID invalide : ${VM_ID} (alphanumérique et tirets, 64 caractères au plus)"

# ----------------------------------------------------------------------------
# Contrôles
# ----------------------------------------------------------------------------

[ "$(id -u)" -eq 0 ] || die "à lancer en root : sudo -E $0"

for f in firecracker jailer vm-config.json; do
    [ -e "${SRC_DIR}/${f}" ] || die "${f} introuvable dans ${SRC_DIR}"
done

# Les disques que vm-config.json déclare (rootfs.ext4, data.ext4...) : chacun est posé dans
# la jail, et chaque drive y pointe vers son propre fichier.
mapfile -t DRIVE_FILES < <(python3 - "${SRC_DIR}/vm-config.json" <<'PY'
import json, os, sys

names = [os.path.basename(d["path_on_host"]) for d in json.load(open(sys.argv[1])).get("drives", [])]
if len(set(names)) != len(names):
    sys.exit("deux drives portent le même nom de fichier : " + ", ".join(names))
print(*names, sep="\n")
PY
)
[ "${#DRIVE_FILES[@]}" -gt 0 ] || die "vm-config.json ne déclare aucun drive"
for f in "${DRIVE_FILES[@]}"; do
    [ -e "${SRC_DIR}/${f}" ] || die "${f} introuvable dans ${SRC_DIR} (drive de vm-config.json)"
done

KERNEL_FILE="$(find "$SRC_DIR" -maxdepth 1 -name 'vmlinux-*' -type f -printf '%f\n' \
               | sort -V | tail -1)"
[ -n "$KERNEL_FILE" ] || die "aucun vmlinux-* dans ${SRC_DIR}"

# cgroup v2 est le défaut sur Ubuntu 22.04+. Le jailer utilise v1 sauf
# indication contraire, d'où la détection.
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
    CGROUP_VERSION=2
else
    CGROUP_VERSION=1
fi
log "cgroup v${CGROUP_VERSION}"

# ----------------------------------------------------------------------------
# Préparation de la jail
#
# Le jailer crée <chroot_dir> et y copie firecracker, mais rien d'autre : les
# ressources de la VM sont à notre charge.
#
# On privilégie le lien physique à la copie — instantané et sans duplication
# sur disque — avec repli sur cp si /srv et le dossier source sont sur des
# systèmes de fichiers différents.
#
# ATTENTION : un lien physique sur rootfs.ext4 signifie que la VM écrit dans
# TON image. Pour des VMs jetables, force la copie (LINK=0) ou passe le drive
# en read-only avec un overlay.
# ----------------------------------------------------------------------------

log "Jail : ${CHROOT_DIR}"

# Un id doit être unique. Si le répertoire existe, on repart proprement plutôt
# que de mélanger les états.
if [ -d "${CHROOT_BASE}/firecracker/${VM_ID}" ]; then
    log "Nettoyage de la jail existante"
    rm -rf "${CHROOT_BASE:?}/firecracker/${VM_ID}"
fi

mkdir -p "$CHROOT_DIR"

place() {
    local src="$1" dst="$2"
    if [ "${LINK:-1}" = "1" ] && ln "$src" "$dst" 2>/dev/null; then
        return
    fi
    if [ "${LINK:-1}" = "1" ]; then
        warn "$(basename "$src") copié faute de lien physique : l'état écrit dans la jail ne survit pas à son nettoyage"
    fi
    cp --reflink=auto "$src" "$dst"
}

place "${SRC_DIR}/${KERNEL_FILE}" "${CHROOT_DIR}/${KERNEL_FILE}"
for f in "${DRIVE_FILES[@]}"; do
    place "${SRC_DIR}/${f}" "${CHROOT_DIR}/${f}"
done

# La configuration est réécrite plutôt que copiée : les chemins doivent être
# relatifs à la racine de la jail, et le nom du kernel varie.
python3 - "$SRC_DIR/vm-config.json" "$CHROOT_DIR/vm-config.json" "$KERNEL_FILE" <<'PY'
import json, os, sys

src, dst, kernel = sys.argv[1:4]
cfg = json.load(open(src))

# Chemins vus depuis l'intérieur de la jail.
cfg["boot-source"]["kernel_image_path"] = kernel
for d in cfg.get("drives", []):
    d["path_on_host"] = os.path.basename(d["path_on_host"])
if "vsock" in cfg:
    cfg["vsock"]["uds_path"] = "v.sock"

json.dump(cfg, open(dst, "w"), indent=2)
PY

# Le jailer chown la racine de la jail et les devices, mais pas ce qu'on y a
# déposé. Sans ce chown, firecracker (déprivilégié) ne peut pas ouvrir son
# rootfs et échoue sur une erreur de backing file.
chown -R "${JAIL_UID}:${JAIL_GID}" "${CHROOT_DIR}"
for f in "${DRIVE_FILES[@]}"; do
    chmod 600 "${CHROOT_DIR}/${f}"
done

log "Contenu de la jail :"
ls -l "$CHROOT_DIR" | sed 's/^/    /'

# ----------------------------------------------------------------------------
# Lancement
#
# Tout ce qui suit `--` est transmis tel quel à firecracker. Le jailer y
# ajoute automatiquement --id, --start-time-us et --start-time-cpu-us.
#
# --resource-limit fsize plafonne la taille des fichiers créés : borne les
# dégâts d'un remplissage de disque depuis la VM. À calibrer au-dessus de la
# taille du rootfs, sinon les écritures de la VM échouent.
# ----------------------------------------------------------------------------

CGROUP_ARGS=()
if [ "$CGROUP_VERSION" = "2" ]; then
    CGROUP_ARGS=(
        --cgroup-version 2
        --cgroup "cpu.max=${CPU_MAX}"
        --cgroup "memory.max=${MEM_MAX}"
    )
else
    CGROUP_ARGS=(
        --cgroup-version 1
        --cgroup "cpuset.mems=0"
        --cgroup "memory.limit_in_bytes=${MEM_MAX}"
    )
fi

# En mode interactif on n'ajoute ni --new-pid-ns ni --daemonize, sans quoi le
# jailer rendrait la main aussitôt et la console série serait perdue.
MODE_ARGS=()
if [ "$INTERACTIVE" = "1" ]; then
    log "Mode interactif — console série attachée, Ctrl-C ou 'reboot' pour arrêter"
else
    MODE_ARGS=(--new-pid-ns --daemonize)
    log "Mode détaché — pilotage par l'API, pas de console"
fi

log "Démarrage (id=${VM_ID}, uid=${JAIL_UID}:${JAIL_GID})"
log "Socket API hôte   : ${CHROOT_DIR}/fc.sock"
log "Socket vsock hôte : ${CHROOT_DIR}/v.sock"
echo

exec "${SRC_DIR}/jailer" \
    --id "$VM_ID" \
    --exec-file "${SRC_DIR}/firecracker" \
    --uid "$JAIL_UID" \
    --gid "$JAIL_GID" \
    --chroot-base-dir "$CHROOT_BASE" \
    "${CGROUP_ARGS[@]}" \
    "${MODE_ARGS[@]}" \
    --resource-limit "fsize=4294967296" \
    --resource-limit "no-file=1024" \
    -- \
    --api-sock fc.sock \
    --config-file vm-config.json
