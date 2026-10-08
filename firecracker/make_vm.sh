#!/usr/bin/env bash
#
# ============================================================================
# make_vm.sh — prépare une microVM Firecracker dans son propre dossier
# ============================================================================
#
# USAGE
#
#   ./make_vm.sh <dossier-vm> [options]
#
#   ./make_vm.sh agent-01                  -> ./vms/agent-01/
#   ./make_vm.sh ./vms/agent-02 --mem 2048
#   ./make_vm.sh agent-01 --slim           -> reconstruit sans garder l'arbre
#
# Un nom sans « / » est résolu sous ./vms/. Un chemin (relatif ou absolu) est
# utilisé tel quel : rien n'oblige les VMs à vivre sous le dossier du script.
#
# PRINCIPE GÉNÉRAL
#
# Une microVM Firecracker a besoin de trois choses :
#   1. le VMM lui-même (le binaire `firecracker`)
#   2. un noyau Linux non compressé au format ELF (vmlinux, pas bzImage)
#   3. une image disque contenant le système de fichiers racine
#
# Amazon publie les deux derniers dans un bucket S3 public (`spec.ccfc.min`)
# utilisé par la CI du projet. Le rootfs y est fourni au format squashfs, qui
# est en lecture seule : on doit donc l'extraire dans un arbre de fichiers
# modifiable, y appliquer nos personnalisations, puis reconstruire une image
# ext4 à partir de cet arbre.
#
#   ubuntu-24.04.squashfs  --unsquashfs-->  rootfs-tree/  --mkfs.ext4 -d-->  rootfs.ext4
#        (lecture seule)                    (modifiable)                     (bootable)
#
# SÉPARATION CACHE / VM
#
# Les artefacts téléchargés (binaires, kernel, squashfs) et l'arbre extrait de
# référence ne dépendent d'aucune VM en particulier : ils vivent une seule fois
# dans un cache partagé. Ce qui est propre à une VM — son arbre personnalisé,
# ses images disque, sa config, son état — vit dans le dossier de la VM.
#
#   <base>/                        dossier contenant ce script
#   ├── make_vm.sh
#   ├── cache/                     PARTAGÉ, téléchargé/extrait une seule fois
#   │   ├── firecracker
#   │   ├── jailer
#   │   ├── vmlinux-<version>
#   │   ├── ubuntu-<version>.squashfs
#   │   └── rootfs-tree/           arbre de référence, jamais modifié
#   └── vms/
#       └── agent-01/              TOUT le nécessaire à l'exécution
#           ├── firecracker        copie locale : le dossier est autonome
#           ├── jailer
#           ├── vmlinux-<version>
#           ├── rootfs-tree/       arbre PROPRE à cette VM (point d'injection)
#           ├── rootfs.ext4
#           ├── data.ext4
#           ├── vm-config.json
#           ├── vm.env             paramètres mémorisés de cette VM
#           └── run.sh
#
# Le dossier de VM ne référence rien hors de lui-même : `tar czf agent-01.tgz`
# ou `mv` vers une autre machine suffit, et `./run.sh` fonctionne toujours.
#
# POURQUOI UN ARBRE PAR VM PLUTÔT QU'UN ARBRE PARTAGÉ
#
# rootfs-tree/ est le point d'injection du code de l'agent. Le partager entre
# VMs signifierait que toutes embarquent le même agent — exactement ce qu'on
# veut éviter. La copie depuis le cache utilise `--reflink=auto` : instantanée
# et sans coût disque sur btrfs/XFS, copie réelle (~1 Go) ailleurs. Avec
# `--slim`, l'arbre est supprimé après construction de l'image : le dossier ne
# garde alors que le strict nécessaire à l'exécution.
#
# SERVICE D'EXÉCUTION (execd)
#
# Le rootfs embarque de quoi accueillir execd, le service invité qui reçoit un
# arbre de code et des fichiers par vsock, exécute une fonction sous contrainte
# de ressources, et rend le résultat. Trois éléments sont posés ici :
#
#   - l'utilisateur non privilégié `sandbox` (uid 1500), vers lequel execd
#     abandonne ses droits avant de lancer le code reçu ;
#   - /data monté noexec,nosuid,nodev : le code applicatif y écrit ses données
#     mais ne peut pas y déposer un binaire et l'exécuter ;
#   - l'unité execd.service, activée UNIQUEMENT si du code a été déposé dans
#     /opt/execd — sinon systemd bouclerait sur un ExecStart introuvable.
#
#   ./make_vm.sh agent-01 --execd ../execd
#
# Le chemin source est mémorisé dans vm.env : les reconstructions suivantes
# recopient le code sans qu'on ait à repasser l'option.
#
# IDEMPOTENCE
#
# Chaque étape vérifie la présence de son résultat avant d'agir. Relancer le
# script ne retélécharge rien, ne réextrait pas le cache, et ne re-clone pas
# l'arbre de la VM. Seule rootfs.ext4 est systématiquement reconstruite, parce
# que c'est précisément l'opération qu'on répète après chaque injection.
#
# data.ext4 fait exception et n'est JAMAIS recréé s'il existe : c'est le seul
# fichier qui porte de l'état produit par l'invité (--reset-data pour forcer).
#
# PRIVILÈGES
#
# sudo est nécessaire pour `unsquashfs` (préservation des uid/gid et des
# fichiers spéciaux de /dev), pour la copie de l'arbre, et pour `mkfs.ext4 -d`
# (lecture de l'arbre appartenant à root).
#
# ============================================================================

set -euo pipefail
#  -e            arrêt à la première commande en échec
#  -u            erreur sur variable non définie (attrape les fautes de frappe)
#  -o pipefail   un pipe échoue si n'importe quel maillon échoue, pas
#                seulement le dernier — critique ici car on chaîne
#                curl | tr | grep un peu partout

# ----------------------------------------------------------------------------
# Emplacements. BASE_DIR est le dossier du SCRIPT, pas le répertoire courant :
# le script doit se comporter pareil qu'on l'invoque depuis n'importe où, et le
# cache doit rester au même endroit dans tous les cas.
# ----------------------------------------------------------------------------

BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CACHE_DIR="${CACHE_DIR:-${BASE_DIR}/cache}"
VMS_DIR="${VMS_DIR:-${BASE_DIR}/vms}"

BUCKET="https://s3.amazonaws.com/spec.ccfc.min"

# Filet de sécurité si la découverte automatique du préfixe échoue (changement
# de format XML côté AWS, réseau capricieux). Sans ce repli, le script
# s'arrêterait au lieu de produire une VM fonctionnelle.
CI_PREFIX_FALLBACK="firecracker-ci/v1.15/"

# Initialisé dès maintenant : discover_ci_prefix n'est appelé que si un
# artefact manque, et `set -u` ferait échouer toute référence à une variable
# jamais définie sur un cache déjà chaud.
CI_PREFIX="$CI_PREFIX_FALLBACK"

# ----------------------------------------------------------------------------
# Paramètres par défaut d'une VM. Ils ne s'appliquent qu'à la CRÉATION : une VM
# existante recharge les siens depuis vm.env, sauf option explicite en ligne de
# commande. Sans ça, un `./make_vm.sh agent-01` sans argument réinitialiserait
# silencieusement une VM configurée à 4 vCPU / 4 Go.
# ----------------------------------------------------------------------------

DEF_ROOTFS_SIZE="2G"      # taille de l'image ext4 (pas de son contenu)

# 2 Go : /data porte les workdirs de execd (code poussé, fichiers d'entrée,
# fichiers produits). Les 200 Mo de la version précédente suffisaient pour de
# l'état d'agent, mais un seul job manipulant des fichiers les sature. Le
# fichier étant creux, une taille généreuse ne coûte rien tant qu'elle n'est
# pas utilisée.
DEF_DATA_SIZE="2G"        # taille du volume de données monté sur /data
DEF_DATA_LABEL="fcdata"   # label ext4 — sert à monter sans dépendre de /dev/vdX
DEF_VCPU_COUNT="2"
DEF_MEM_SIZE_MIB="1024"
DEF_EXECD_PORT="5100"     # port vsock d'écoute de execd (5000 = executor.py)

# Compte non privilégié sous lequel execd exécute le code reçu. L'uid est fixé
# en dur plutôt que laissé à un `useradd` : l'arbre est construit hors ligne,
# et une valeur figée rend les images reproductibles d'une VM à l'autre.
SANDBOX_USER="sandbox"
SANDBOX_UID="1500"
SANDBOX_GID="1500"

# Point d'entrée attendu dans /opt/execd. Sa présence conditionne l'activation
# de l'unité systemd.
EXECD_ENTRY="execd.py"

# Dossier du service, cherché à côté de ce script quand --execd n'est pas
# donné et qu'aucune source n'est mémorisée dans vm.env. C'est la disposition
# usuelle du dépôt : make_vm.sh, jailer-run.sh et service/ côte à côte.
DEF_EXECD_DIRNAME="service"

# Options de ligne de commande (vides = « ne pas surcharger vm.env »)
OPT_ROOTFS_SIZE="" ; OPT_DATA_SIZE=""  ; OPT_DATA_LABEL=""
OPT_VCPU_COUNT=""  ; OPT_MEM_SIZE=""   ; OPT_GUEST_CID=""
OPT_EXECD_SRC=""   ; OPT_EXECD_PORT=""
SLIM=0 ; RESET_DATA=0 ; RESET_TREE=0 ; NO_EXECD=0

# ----------------------------------------------------------------------------
# Affichage
# ----------------------------------------------------------------------------

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat >&2 <<'USAGE'
Usage : ./make_vm.sh <dossier-vm> [options]

  <dossier-vm>        nom simple  -> ./vms/<nom>/
                      chemin      -> utilisé tel quel

Options
  --vcpu N            nombre de vCPU
  --mem MIB           mémoire en MiB
  --cid N             CID vsock de l'invité (auto-attribué à la création)
  --rootfs-size TAILLE  taille de rootfs.ext4        (ex: 4G)
  --data-size TAILLE    taille de data.ext4          (ex: 4G)
  --data-label NOM      label ext4 du volume /data
  --execd DIR         source du service d'exécution -> /opt/execd
                      (défaut : ./service si présent ; chemin mémorisé et
                       recopié à chaque reconstruction)
  --execd-port N      port vsock d'écoute de execd (défaut 5100)
  --no-execd          désactive le service et retire /opt/execd de l'arbre
  --reset-data        recrée data.ext4 (DESTRUCTIF : efface l'état invité)
  --reset-tree        re-clone rootfs-tree depuis le cache (DESTRUCTIF :
                      efface les injections faites dans cette VM)
  --slim              supprime rootfs-tree après construction de l'image
  --cache DIR         emplacement du cache partagé
  -h, --help          cette aide

Exemples
  ./make_vm.sh agent-01
  ./make_vm.sh agent-02 --vcpu 4 --mem 4096 --rootfs-size 4G
  ./make_vm.sh agent-01 --execd ../autre-service
  ./make_vm.sh agent-01 --slim
USAGE
    exit 1
}

# ----------------------------------------------------------------------------
# parse_args
#
# Le premier argument non-option est le dossier de la VM. On accepte qu'il
# arrive après les options pour ne pas imposer d'ordre à l'utilisateur.
#
# Définit : VM_ARG et les OPT_*
# ----------------------------------------------------------------------------
parse_args() {
    VM_ARG=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --vcpu)         OPT_VCPU_COUNT="${2:?valeur manquante}" ; shift 2 ;;
            --mem)          OPT_MEM_SIZE="${2:?valeur manquante}"   ; shift 2 ;;
            --cid)
                # Contrôle syntaxique immédiat : validate_cid ne tourne
                # qu'après check_prereqs, donc après le prompt sudo — attendre
                # un mot de passe pour annoncer une faute de frappe est
                # inutilement pénible.
                case "${2:?valeur manquante}" in
                    ''|*[!0-9]*) die "CID invalide : « $2 » — entier attendu" ;;
                esac
                OPT_GUEST_CID="$2" ; shift 2 ;;
            --rootfs-size)  OPT_ROOTFS_SIZE="${2:?valeur manquante}"; shift 2 ;;
            --data-size)    OPT_DATA_SIZE="${2:?valeur manquante}"  ; shift 2 ;;
            --data-label)   OPT_DATA_LABEL="${2:?valeur manquante}" ; shift 2 ;;
            --execd)
                # Résolu tout de suite en absolu : le chemin est mémorisé dans
                # vm.env, et une valeur relative au répertoire courant de
                # l'invocation ne voudrait plus rien dire au prochain appel.
                [ -d "${2:?valeur manquante}" ] || die "--execd : dossier introuvable : $2"
                OPT_EXECD_SRC="$(cd -- "$2" && pwd)" ; shift 2 ;;
            --execd-port)
                case "${2:?valeur manquante}" in
                    ''|*[!0-9]*) die "port invalide : « $2 » — entier attendu" ;;
                esac
                [ "$2" -ge 1 ] && [ "$2" -le 4294967294 ] \
                    || die "port hors plage vsock : $2"
                OPT_EXECD_PORT="$2" ; shift 2 ;;
            --no-execd)     NO_EXECD=1    ; shift ;;
            --cache)        CACHE_DIR="${2:?valeur manquante}"      ; shift 2 ;;
            --reset-data)   RESET_DATA=1  ; shift ;;
            --reset-tree)   RESET_TREE=1  ; shift ;;
            --slim)         SLIM=1        ; shift ;;
            -h|--help)      usage ;;
            -*)             die "option inconnue : $1" ;;
            *)
                [ -z "$VM_ARG" ] || die "un seul dossier de VM attendu (reçu « $VM_ARG » puis « $1 »)"
                VM_ARG="$1" ; shift ;;
        esac
    done

    [ -n "$VM_ARG" ] || usage
}

# ----------------------------------------------------------------------------
# resolve_vm_dir
#
# Un argument sans « / » désigne un nom de VM et atterrit sous vms/. Avec un
# « / », c'est un chemin que l'on respecte, ce qui permet de placer les VMs sur
# un autre volume (utile : rootfs-tree pèse ~1 Go par VM).
#
# Le nom sert aussi de hostname invité, d'où le filtrage sur les caractères
# admis par RFC 1123 — un hostname invalide fait échouer systemd-hostnamed au
# boot.
#
# Définit : VM_DIR, VM_NAME, VM_TREE
# ----------------------------------------------------------------------------
resolve_vm_dir() {
    case "$VM_ARG" in
        */*|.|..) VM_DIR="$VM_ARG" ;;
        *)        VM_DIR="${VMS_DIR}/${VM_ARG}" ;;
    esac

    # On crée le dossier avant `cd` pour pouvoir en obtenir le chemin absolu :
    # tous les chemins manipulés ensuite doivent être absolus, sinon un `cd`
    # interne casserait les références.
    mkdir -p "$VM_DIR"
    VM_DIR="$(cd -- "$VM_DIR" && pwd)"

    VM_NAME="$(basename "$VM_DIR" | tr -c 'a-zA-Z0-9-' '-' | sed 's/-*$//')"
    [ -n "$VM_NAME" ] || die "nom de VM invalide : $VM_ARG"

    VM_TREE="${VM_DIR}/rootfs-tree"
}

# ----------------------------------------------------------------------------
# load_vm_env / save_vm_env
#
# vm.env est la mémoire de la VM. Sans lui, il faudrait repasser les mêmes
# variables d'environnement à chaque reconstruction, et une omission
# reconfigurerait la VM en silence.
#
# Le fichier n'est que des affectations shell, donc `source`able tel quel — y
# compris par un futur orchestrateur qui voudra connaître le CID d'une VM sans
# parser vm-config.json.
# ----------------------------------------------------------------------------
load_vm_env() {
    ROOTFS_SIZE="$DEF_ROOTFS_SIZE"
    DATA_SIZE="$DEF_DATA_SIZE"
    DATA_LABEL="$DEF_DATA_LABEL"
    VCPU_COUNT="$DEF_VCPU_COUNT"
    MEM_SIZE_MIB="$DEF_MEM_SIZE_MIB"
    EXECD_PORT="$DEF_EXECD_PORT"
    EXECD_SRC=""
    GUEST_CID=""
    KERNEL_FILE=""

    if [ -f "${VM_DIR}/vm.env" ]; then
        log "Configuration existante : ${VM_DIR}/vm.env"

        local dir_name="$VM_NAME"
        # shellcheck disable=SC1091
        . "${VM_DIR}/vm.env"

        # Le dossier fait autorité sur le nom : renommer le dossier doit
        # renommer la VM (et donc son hostname), sinon la valeur mémorisée
        # gagnerait et le renommage n'aurait aucun effet visible.
        if [ "$VM_NAME" != "$dir_name" ]; then
            log "VM renommée : ${VM_NAME} -> ${dir_name}"
            VM_NAME="$dir_name"
        fi
    else
        VM_IS_NEW=1
    fi

    [ -n "$OPT_ROOTFS_SIZE" ] && ROOTFS_SIZE="$OPT_ROOTFS_SIZE"
    [ -n "$OPT_DATA_SIZE" ]   && DATA_SIZE="$OPT_DATA_SIZE"
    [ -n "$OPT_DATA_LABEL" ]  && DATA_LABEL="$OPT_DATA_LABEL"
    [ -n "$OPT_VCPU_COUNT" ]  && VCPU_COUNT="$OPT_VCPU_COUNT"
    [ -n "$OPT_MEM_SIZE" ]    && MEM_SIZE_MIB="$OPT_MEM_SIZE"
    [ -n "$OPT_GUEST_CID" ]   && GUEST_CID="$OPT_GUEST_CID"
    [ -n "$OPT_EXECD_SRC" ]   && EXECD_SRC="$OPT_EXECD_SRC"
    [ -n "$OPT_EXECD_PORT" ]  && EXECD_PORT="$OPT_EXECD_PORT"

    # --no-execd oublie la source mémorisée : sans ça, la reconstruction
    # suivante sans option la recopierait et réactiverait le service, alors que
    # l'utilisateur vient explicitement de le retirer.
    [ "$NO_EXECD" -eq 1 ] && EXECD_SRC=""

    # Détection du dossier du service, en dernier recours seulement : l'option
    # explicite prime, puis la valeur mémorisée dans vm.env, puis la
    # convention. Dans cet ordre — sinon un dossier `service/` oublié dans le
    # dépôt écraserait silencieusement un chemin choisi exprès.
    if [ "$NO_EXECD" -eq 0 ] && [ -z "$EXECD_SRC" ]; then
        local candidate="${BASE_DIR}/${DEF_EXECD_DIRNAME}"
        if [ -f "${candidate}/${EXECD_ENTRY}" ]; then
            EXECD_SRC="$candidate"
            log "Service détecté : ${DEF_EXECD_DIRNAME}/ (--execd non nécessaire)"
        elif [ -d "$candidate" ]; then
            # Le dossier existe mais ne contient pas le point d'entrée : c'est
            # presque toujours une erreur de nom de fichier, et le signaler ici
            # évite de la découvrir par un vsock muet après le boot.
            warn "${DEF_EXECD_DIRNAME}/ existe mais ${EXECD_ENTRY} y est absent — service ignoré"
        fi
    fi

    [ -n "$GUEST_CID" ] || GUEST_CID="$(next_free_cid)"
    validate_cid

    return 0   # les `[ ]` ci-dessus laisseraient un statut non nul sous set -e
}

save_vm_env() {
    cat > "${VM_DIR}/vm.env" <<ENV
# Paramètres de la VM « ${VM_NAME} » — relus par make_vm.sh et exploitables
# par un orchestrateur. Édition manuelle possible ; relancer make_vm.sh
# ensuite pour régénérer vm-config.json.
VM_NAME="${VM_NAME}"
KERNEL_FILE="${KERNEL_FILE}"
ROOTFS_SIZE="${ROOTFS_SIZE}"
DATA_SIZE="${DATA_SIZE}"
DATA_LABEL="${DATA_LABEL}"
VCPU_COUNT="${VCPU_COUNT}"
MEM_SIZE_MIB="${MEM_SIZE_MIB}"
GUEST_CID="${GUEST_CID}"

# Service d'exécution embarqué. EXECD_SRC vide = pas de service dans l'image.
# EXECD_PORT est la valeur que le client hôte doit utiliser dans le handshake
# Firecracker (« CONNECT <port>\\n ») : à lire ici plutôt qu'à coder en dur.
EXECD_SRC="${EXECD_SRC}"
EXECD_PORT="${EXECD_PORT}"
SANDBOX_UID="${SANDBOX_UID}"

# Chemins d'accès depuis l'hôte. À LIRE, jamais à reconstruire : sous jailer
# les sockets se retrouvent dans le chroot, et toute app qui recompose
# « <dossier-vm>/v.sock » casserait ce jour-là. make_vm.sh réécrit ces valeurs.
API_SOCK="${VM_DIR}/runtime/fc.sock"
VSOCK_UDS="${VM_DIR}/runtime/v.sock"
ENV
}

# ----------------------------------------------------------------------------
# vm_env_files
#
# Émet le vm.env de chaque VM voisine, en chemins canoniques, dédupliqués, et
# sans celui de la VM courante — sinon elle se compterait elle-même et se
# signalerait sa propre collision.
#
# Trois racines sont balayées, parce qu'une VM peut légitimement vivre
# ailleurs que sous vms/ : le dossier de VMs par défaut, le dossier du script
# (layout à plat), et le parent de la VM courante (regroupement maison sur un
# autre volume). C'est ce dernier qui manquait dans la première version : deux
# VMs sœurs créées par chemin explicite ne se voyaient pas, et chacune
# repartait du CID 3.
#
# Limite assumée : une VM isolée dans une arborescence sans voisine échappe au
# scan. Une allocation réellement fiable demanderait un registre central ;
# tant qu'il n'existe pas, --cid est l'échappatoire.
# ----------------------------------------------------------------------------
vm_env_files() {
    local parent f d
    parent="$(dirname "$VM_DIR")"

    for f in "${VMS_DIR}"/*/vm.env "${BASE_DIR}"/*/vm.env "${parent}"/*/vm.env; do
        [ -f "$f" ] || continue
        d="$(cd -- "$(dirname "$f")" && pwd)"
        [ "$d" = "$VM_DIR" ] && continue
        printf '%s\n' "${d}/vm.env"
    done | sort -u
}

# ----------------------------------------------------------------------------
# next_free_cid
#
# Premier CID vsock non utilisé par une VM connue.
#
# Les CID 0 à 2 sont réservés (0=hyperviseur, 1=local, 2=hôte), on part donc
# de 3. Ce n'est pas une contrainte de Firecracker : son vsock est implémenté
# en espace utilisateur au-dessus d'un socket Unix, et c'est le CHEMIN de cet
# UDS qui identifie la VM côté hôte — deux VMs peuvent partager un CID sans se
# gêner. La valeur sert d'identifiant stable dès qu'un orchestrateur désigne
# une VM par son CID dans ses logs ou sa table d'état.
# ----------------------------------------------------------------------------
next_free_cid() {
    local used cid
    used="$(vm_env_files | while IFS= read -r f; do
                grep -h '^GUEST_CID=' "$f" 2>/dev/null | cut -d'"' -f2
            done)"

    cid=3
    while printf '%s\n' "$used" | grep -qx "$cid"; do
        cid=$((cid + 1))
    done
    printf '%s' "$cid"
}

# ----------------------------------------------------------------------------
# validate_cid
#
# Une valeur hors plage est rejetée par l'API Firecracker au boot, avec un
# message qui n'oriente pas ; autant échouer ici. Un doublon, lui, n'empêche
# rien de fonctionner mais rend deux VMs indiscernables pour l'orchestrateur —
# on se contente donc de le signaler.
#
# Plage utile : 3 à 2^32-2 (0xFFFFFFFF vaut VMADDR_CID_ANY, ce n'est pas une
# adresse d'invité).
# ----------------------------------------------------------------------------
validate_cid() {
    case "$GUEST_CID" in
        ''|*[!0-9]*) die "CID invalide : « ${GUEST_CID} » — entier attendu" ;;
    esac

    [ "$GUEST_CID" -ge 3 ] \
        || die "CID ${GUEST_CID} réservé (0=hyperviseur, 1=local, 2=hôte) — 3 minimum"
    [ "$GUEST_CID" -lt 4294967295 ] \
        || die "CID ${GUEST_CID} : 0xFFFFFFFF est VMADDR_CID_ANY, pas une adresse d'invité"

    local f
    while IFS= read -r f; do
        if grep -qs "^GUEST_CID=\"${GUEST_CID}\"$" "$f"; then
            warn "CID ${GUEST_CID} déjà utilisé par « $(basename "$(dirname "$f")") »"
            warn "  sans effet sur Firecracker (chaque VM a son propre UDS),"
            warn "  mais deux VMs indiscernables si l'orchestrateur les indexe par CID"
        fi
    done < <(vm_env_files)

    return 0
}

# ----------------------------------------------------------------------------
# check_prereqs
#
# Valide l'environnement AVANT tout téléchargement, pour échouer vite plutôt
# que 300 Mo plus tard.
#
# Trois catégories de contrôles :
#
#   - Outils : curl et jq pour l'API GitHub, squashfs-tools pour l'extraction,
#     e2fsprogs pour construire et inspecter l'ext4.
#
#   - Architecture : les artefacts du bucket sont publiés par arch. `uname -m`
#     donne directement la chaîne utilisée dans les chemins S3 (x86_64,
#     aarch64), pas de traduction nécessaire.
#
#   - KVM : Firecracker est un VMM de type 2, il délègue la virtualisation à
#     /dev/kvm. Absence du device = virtualisation désactivée dans le BIOS ou
#     machine déjà virtualisée sans nested virt. Device présent mais non
#     accessible = simple problème de permissions, on avertit sans bloquer
#     puisque l'utilisateur peut préparer les fichiers maintenant et corriger
#     les droits avant de lancer la VM.
#
# Définit : ARCH
# ----------------------------------------------------------------------------
need() {
    command -v "$1" >/dev/null 2>&1 \
        || die "commande manquante : $1 — installe-la avec : sudo apt install -y $2"
}

check_prereqs() {
    log "Vérification des pré-requis"

    need curl       curl
    need jq         jq
    need unsquashfs squashfs-tools
    need mkfs.ext4  e2fsprogs
    need debugfs    e2fsprogs

    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64|aarch64) ;;
        *) die "architecture non supportée par Firecracker : $ARCH" ;;
    esac

    [ -e /dev/kvm ] || die "/dev/kvm absent — virtualisation matérielle désactivée ?"

    if [ ! -r /dev/kvm ] || [ ! -w /dev/kvm ]; then
        warn "/dev/kvm non accessible en lecture/écriture pour ${USER}"
        warn "  ponctuel  : sudo setfacl -m u:${USER}:rw /dev/kvm"
        warn "  permanent : sudo usermod -aG kvm ${USER}  (puis reconnexion)"
    fi

    # Demande le mot de passe maintenant et met en cache le ticket sudo, pour
    # éviter que le script se bloque sur un prompt au milieu d'un téléchargement.
    sudo -v || die "sudo requis pour unsquashfs et mkfs.ext4"

    mkdir -p "$CACHE_DIR"

    log "Architecture : ${ARCH}"
}

# ----------------------------------------------------------------------------
# adopt_legacy_artifacts
#
# Migration depuis l'ancienne disposition « tout à plat » : si des artefacts
# traînent à la racine, on les déplace dans le cache au lieu de les
# retélécharger. Un `mv` sur le même système de fichiers est instantané, et
# ça évite de refaire 300 Mo de réseau pour une simple réorganisation.
#
# On ne touche PAS aux rootfs.ext4 / data.ext4 / vm-config.json de l'ancien
# layout : ce sont des artefacts de VM, pas des artefacts partagés, et leur
# destination dépend d'une décision qui appartient à l'utilisateur.
# ----------------------------------------------------------------------------
adopt_legacy_artifacts() {
    local moved=0 f

    for f in firecracker jailer; do
        if [ -x "${BASE_DIR}/${f}" ] && [ ! -e "${CACHE_DIR}/${f}" ]; then
            mv "${BASE_DIR}/${f}" "${CACHE_DIR}/${f}" ; moved=1
        fi
    done

    for f in "${BASE_DIR}"/vmlinux-* "${BASE_DIR}"/*.squashfs; do
        [ -f "$f" ] || continue
        if [ ! -e "${CACHE_DIR}/$(basename "$f")" ]; then
            mv "$f" "${CACHE_DIR}/" ; moved=1
        fi
    done

    # L'arbre appartient à root : le déplacer demande sudo.
    #
    # Attention : cet arbre-là a pu être personnalisé (injection de /opt/agent
    # par exemple). En devenant le template partagé, ses modifications seront
    # héritées par TOUTES les VMs créées ensuite. C'est parfois voulu — un
    # runtime commun — mais jamais évident, d'où l'avertissement.
    if [ -d "${BASE_DIR}/rootfs-tree" ] && [ ! -d "${CACHE_DIR}/rootfs-tree" ]; then
        sudo mv "${BASE_DIR}/rootfs-tree" "${CACHE_DIR}/rootfs-tree" ; moved=1
        warn "rootfs-tree repris comme template partagé — ses personnalisations"
        warn "  seront héritées par chaque nouvelle VM. Pour repartir d'un arbre"
        warn "  vierge : sudo rm -rf ${CACHE_DIR}/rootfs-tree"
    fi

    [ "$moved" -eq 1 ] && log "Artefacts de l'ancien layout déplacés vers ${CACHE_DIR}"
    return 0
}

# ----------------------------------------------------------------------------
# fetch_firecracker
#
# Installe les binaires `firecracker` et `jailer` depuis la dernière release
# GitHub, dans le cache partagé.
#
# À noter : la version du binaire est INDÉPENDANTE de la version des artefacts
# CI téléchargés plus bas. On peut très bien tourner en 1.16.x avec un kernel
# publié sous le préfixe v1.15/ — la compatibilité est assurée dans ce sens.
#
# L'archive se décompresse en `release-<tag>-<arch>/` contenant des binaires
# suffixés par la version. On les installe sous des noms fixes pour que run.sh
# et vm-config.json n'aient pas à connaître la version courante.
#
# `jailer` encapsule Firecracker dans un chroot + cgroups + namespaces : c'est
# la couche d'isolation à activer quand plusieurs VMs tourneront en parallèle,
# d'où sa copie dans chaque dossier de VM.
# ----------------------------------------------------------------------------
fetch_firecracker() {
    if [ -x "${CACHE_DIR}/firecracker" ]; then
        log "Firecracker en cache : $("${CACHE_DIR}/firecracker" --version | head -1)"
        return
    fi

    log "Récupération de la dernière release Firecracker"

    local tag
    tag="$(curl -fsSL https://api.github.com/repos/firecracker-microvm/firecracker/releases/latest \
           | jq -r '.tag_name')"
    [ -n "$tag" ] && [ "$tag" != "null" ] \
        || die "impossible de déterminer la dernière release (quota API GitHub atteint ?)"

    log "Release : ${tag}"

    local tarball="${CACHE_DIR}/firecracker-${tag}-${ARCH}.tgz"
    curl -fSL --progress-bar -o "$tarball" \
        "https://github.com/firecracker-microvm/firecracker/releases/download/${tag}/firecracker-${tag}-${ARCH}.tgz" \
        || die "téléchargement de firecracker-${tag}-${ARCH}.tgz échoué"

    tar -xzf "$tarball" -C "$CACHE_DIR"

    local reldir="${CACHE_DIR}/release-${tag}-${ARCH}"
    [ -d "$reldir" ] || die "répertoire release-${tag}-${ARCH} introuvable dans l'archive"

    install -m 755 "${reldir}/firecracker-${tag}-${ARCH}" "${CACHE_DIR}/firecracker"
    install -m 755 "${reldir}/jailer-${tag}-${ARCH}"      "${CACHE_DIR}/jailer"

    rm -rf "$reldir" "$tarball"

    log "Installé : $("${CACHE_DIR}/firecracker" --version | head -1)"
}

# ----------------------------------------------------------------------------
# s3_keys <préfixe>
#
# Liste les clés d'objets S3 sous un préfixe donné.
#
# Subtilité qui coûte du temps quand on l'ignore : S3 renvoie son XML sur UNE
# SEULE LIGNE. Or grep travaille ligne par ligne et ne renvoie donc qu'une
# correspondance, même avec -o. D'où le `tr '<' '\n'` qui découpe la réponse
# sur les ouvrants de balises avant filtrage — chaque élément se retrouve sur
# sa propre ligne, sous la forme `Key>chemin/vers/objet`.
#
# Le `|| true` neutralise le code de retour 1 de grep quand rien ne
# correspond : sous `set -e`, il ferait avorter le script alors qu'une liste
# vide est un résultat légitime que l'appelant sait traiter.
# ----------------------------------------------------------------------------
s3_keys() {
    curl -fsSL "${BUCKET}/?list-type=2&prefix=$1" \
        | tr '<' '\n' \
        | grep -oP '(?<=^Key>).*' || true
}

# ----------------------------------------------------------------------------
# discover_ci_prefix
#
# Détermine le préfixe de la version d'artefacts CI la plus récente, par
# exemple `firecracker-ci/v1.15/`.
#
# Le paramètre `delimiter=/` demande à S3 de regrouper les clés partageant un
# même segment de chemin et de les renvoyer comme "préfixes communs" (balises
# <Prefix>) au lieu d'énumérer chaque objet — on obtient la liste des versions
# sans télécharger l'inventaire complet du bucket.
#
# `sort -V` (version sort) est indispensable : en tri lexicographique, v1.9
# passerait après v1.15.
#
# Court-circuité si kernel ET squashfs sont déjà en cache : aucun appel réseau
# n'est alors nécessaire, et créer une VM hors ligne doit rester possible.
#
# Définit : CI_PREFIX
# ----------------------------------------------------------------------------
discover_ci_prefix() {
    log "Découverte du préfixe CI le plus récent"

    local prefix
    prefix="$(curl -fsSL "${BUCKET}/?list-type=2&prefix=firecracker-ci/&delimiter=/" \
        | tr '<' '\n' \
        | grep -oP '(?<=^Prefix>)firecracker-ci/v[0-9]+\.[0-9]+/' \
        | sort -V | tail -1 || true)"

    if [ -z "$prefix" ]; then
        warn "découverte impossible, repli sur ${CI_PREFIX_FALLBACK}"
        prefix="$CI_PREFIX_FALLBACK"
    fi

    CI_PREFIX="$prefix"
    log "Préfixe CI : ${CI_PREFIX}"
}

# ----------------------------------------------------------------------------
# fetch_kernel
#
# Télécharge le noyau invité le plus récent disponible sous le préfixe CI.
#
# Firecracker exige un `vmlinux` : un noyau ELF non compressé. Un `bzImage`
# de distribution ne fonctionnera pas — le VMM charge directement les segments
# ELF en mémoire invitée, il n'y a pas de bootloader pour décompresser quoi
# que ce soit. D'où le contrôle `file` en fin de fonction, qui détecte au
# passage un téléchargement tronqué ou une page d'erreur HTML enregistrée à la
# place du binaire.
#
# Le nom du fichier n'est pas codé en dur car il change à chaque publication
# CI ; il est propagé jusqu'à vm-config.json via KERNEL_FILE.
#
# Une VM déjà construite garde SON kernel (celui de vm.env), même si le cache
# en contient un plus récent : changer de noyau sous une VM existante n'est pas
# une décision à prendre implicitement.
#
# Définit : KERNEL_FILE
# ----------------------------------------------------------------------------
fetch_kernel() {
    if [ -n "$KERNEL_FILE" ] && [ -f "${CACHE_DIR}/${KERNEL_FILE}" ]; then
        log "Kernel de la VM : ${KERNEL_FILE}"
        return
    fi

    local existing
    existing="$(find "$CACHE_DIR" -maxdepth 1 -name 'vmlinux-*' -type f | sort -V | tail -1)"

    if [ -n "$existing" ]; then
        KERNEL_FILE="$(basename "$existing")"
        log "Kernel en cache : ${KERNEL_FILE}"
        return
    fi

    log "Recherche du kernel"

    local key
    key="$(s3_keys "${CI_PREFIX}${ARCH}/vmlinux-" \
        | grep -P 'vmlinux-[0-9]+\.[0-9]+\.[0-9]+$' \
        | sort -V | tail -1)"
    [ -n "$key" ] || die "aucun kernel trouvé sous ${CI_PREFIX}${ARCH}/"

    KERNEL_FILE="$(basename "$key")"
    log "Téléchargement : ${KERNEL_FILE}"
    curl -fSL --progress-bar -o "${CACHE_DIR}/${KERNEL_FILE}" "${BUCKET}/${key}"

    file "${CACHE_DIR}/${KERNEL_FILE}" | grep -q 'ELF 64-bit' \
        || die "${KERNEL_FILE} n'est pas un ELF 64-bit — téléchargement corrompu"
}

# ----------------------------------------------------------------------------
# fetch_squashfs
#
# Télécharge l'image rootfs Ubuntu au format squashfs, dans le cache.
#
# C'est la source dont on dérive l'arbre de référence. On la conserve après
# extraction pour pouvoir régénérer un arbre propre sans repasser par le
# réseau, ce qui est utile quand on a cassé le cache en expérimentant.
#
# Définit : SQUASHFS_FILE
# ----------------------------------------------------------------------------
fetch_squashfs() {
    local existing
    existing="$(find "$CACHE_DIR" -maxdepth 1 -name '*.squashfs' -type f | sort -V | tail -1)"

    if [ -n "$existing" ]; then
        SQUASHFS_FILE="$(basename "$existing")"
        log "Squashfs en cache : ${SQUASHFS_FILE}"
        return
    fi

    log "Recherche du rootfs"

    local key
    key="$(s3_keys "${CI_PREFIX}${ARCH}/ubuntu-" \
        | grep -P 'ubuntu-[0-9]+\.[0-9]+\.squashfs$' \
        | sort -V | tail -1)"
    [ -n "$key" ] || die "aucun squashfs trouvé sous ${CI_PREFIX}${ARCH}/"

    SQUASHFS_FILE="$(basename "$key")"
    log "Téléchargement : ${SQUASHFS_FILE}"
    curl -fSL --progress-bar -o "${CACHE_DIR}/${SQUASHFS_FILE}" "${BUCKET}/${key}"

    file "${CACHE_DIR}/${SQUASHFS_FILE}" | grep -qi 'squashfs' \
        || die "${SQUASHFS_FILE} n'est pas un squashfs — téléchargement corrompu"
}

# ----------------------------------------------------------------------------
# extract_template
#
# Déploie le squashfs en arbre de fichiers modifiable, dans le cache.
#
# Cet arbre est le TEMPLATE : il n'est jamais personnalisé, jamais injecté.
# Chaque VM en reçoit une copie qu'elle est libre de modifier. Le garder
# vierge est ce qui permet de créer une VM neuve sans repasser par unsquashfs
# (~20 s) ni par le réseau.
#
# sudo est requis pour que unsquashfs restitue les uid/gid d'origine et
# recrée les nœuds de périphériques sous /dev. Sans lui, tout appartiendrait
# à l'utilisateur courant et le système invité refuserait de démarrer
# correctement (systemd, sudo et les permissions de /tmp y sont sensibles).
# ----------------------------------------------------------------------------
extract_template() {
    if [ -d "${CACHE_DIR}/rootfs-tree" ]; then
        log "Arbre de référence déjà extrait"
        return
    fi

    log "Extraction du squashfs vers ${CACHE_DIR}/rootfs-tree"
    sudo unsquashfs -q -d "${CACHE_DIR}/rootfs-tree" "${CACHE_DIR}/${SQUASHFS_FILE}"
    log "Taille : $(sudo du -sh "${CACHE_DIR}/rootfs-tree" | cut -f1)"
}

# ----------------------------------------------------------------------------
# clone_tree
#
# Donne à la VM sa propre copie de l'arbre de référence.
#
# `--reflink=auto` demande une copie par référence (copy-on-write) quand le
# système de fichiers la supporte : instantanée et sans consommation d'espace
# tant que rien n'est modifié, sur btrfs et XFS (avec reflink=1). Sur ext4,
# l'option retombe silencieusement sur une copie classique — d'où le report du
# temps écoulé, qui rend la différence visible immédiatement.
#
# `-a` préserve uid/gid, modes, liens symboliques et fichiers spéciaux : sans
# lui, la copie produirait un arbre inutilisable pour les mêmes raisons qu'une
# extraction sans sudo.
#
# Jamais re-cloné si présent : ce serait écraser en silence les injections de
# code faites dans cette VM. --reset-tree pour forcer.
# ----------------------------------------------------------------------------
clone_tree() {
    if [ -d "$VM_TREE" ] && [ "$RESET_TREE" -eq 1 ]; then
        log "Suppression de l'arbre existant (--reset-tree)"
        sudo rm -rf "$VM_TREE"
    fi

    if [ -d "$VM_TREE" ]; then
        log "rootfs-tree de la VM déjà présent — conservé avec ses injections"
        return
    fi

    log "Copie de l'arbre de référence vers ${VM_TREE}"

    local t0=$SECONDS
    sudo cp -a --reflink=auto "${CACHE_DIR}/rootfs-tree" "$VM_TREE"
    log "Copié en $((SECONDS - t0)) s ($(sudo du -sh "$VM_TREE" | cut -f1) apparents)"
}

# ----------------------------------------------------------------------------
# configure_rootfs
#
# Applique les personnalisations minimales pour obtenir une VM utilisable.
# Tout se fait par écriture directe dans l'arbre : on ne peut pas exécuter
# `systemctl` ou `apt` ici puisqu'on n'est pas dans le système invité (il
# faudrait un chroot, plus lourd et sensible à l'architecture).
#
# 1) AUTOLOGIN SUR LA CONSOLE SÉRIE
#    La VM n'a que ttyS0 comme console. Sans cette surcharge, systemd y lance
#    un getty classique qui présente une invite `login:` — et le mot de passe
#    root de l'image CI est inconnu. On remplace donc la ligne ExecStart de
#    l'unité serial-getty@ttyS0 par un agetty en --autologin root.
#    La ligne `ExecStart=` vide est obligatoire : dans systemd, elle réinitialise
#    la directive héritée avant d'en définir une nouvelle, sinon les deux
#    s'accumulent et l'unité refuse de démarrer.
#
# 2) NOM D'HÔTE
#    Il vaut le nom du dossier de la VM. Avec plusieurs consoles série
#    ouvertes côte à côte, c'est le seul repère qui distingue un terminal
#    invité d'un autre — et de celui de l'hôte.
#
# 3) SUPPRESSION DE systemd-networkd-wait-online
#    Cette unité attend qu'une interface réseau soit opérationnelle. Comme la
#    VM n'a aucun périphérique réseau (on communique en vsock), elle attend
#    jusqu'à son timeout et retarde le boot de plusieurs secondes pour rien.
#    On retire les symlinks d'activation dans les .wants/.
#
# 4) CONTRÔLE DE PYTHON
#    Simple avertissement : l'agent qui sera injecté plus tard en dépend.
#    L'image CI Ubuntu 24.04 embarque python3.12 complet, mais autant le
#    vérifier plutôt que de le découvrir au premier démarrage de l'agent.
# ----------------------------------------------------------------------------
configure_rootfs() {
    log "Configuration du rootfs (hostname : ${VM_NAME})"

    local tree="$VM_TREE"
    local dropin="${tree}/etc/systemd/system/serial-getty@ttyS0.service.d"

    sudo mkdir -p "$dropin"
    sudo tee "${dropin}/autologin.conf" >/dev/null <<'CONF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear %I 115200 linux
CONF

    # `sudo tee` et `sudo mkdir` créent en root:root, mais avec le umask du
    # shell appelant : on fixe les modes explicitement pour ne pas dépendre de
    # l'environnement de l'utilisateur.
    sudo chown root:root "$dropin" "${dropin}/autologin.conf"
    sudo chmod 755 "$dropin"
    sudo chmod 644 "${dropin}/autologin.conf"

    echo "$VM_NAME" | sudo tee "${tree}/etc/hostname" >/dev/null
    sudo chown root:root "${tree}/etc/hostname"
    sudo chmod 644 "${tree}/etc/hostname"

    # --- Extinction rapide ---------------------------------------------------
    #
    # systemd accorde 90 s à chaque unité pour se terminer avant de la tuer.
    # Sur une VM jetable c'est absurde : l'orchestrateur attendrait une minique
    # et demie avant de pouvoir réutiliser la place, et le shell root laissé
    # par l'autologin suffit à déclencher cette attente. 5 s laissent le temps
    # de vider les caches et démonter /data, ce qui est tout ce qui compte ici.
    #
    # C'est ce réglage qui rend SendCtrlAltDel utilisable : sans lui, l'arrêt
    # dépasse le timeout de l'orchestrateur, qui bascule sur SIGTERM et coupe
    # l'invité net, écritures en vol comprises.
    sudo mkdir -p "${tree}/etc/systemd/system.conf.d"
    sudo tee "${tree}/etc/systemd/system.conf.d/fast-shutdown.conf" >/dev/null <<'CONF'
[Manager]
DefaultTimeoutStopSec=5s
DefaultTimeoutStartSec=15s
CONF
    sudo chown -R root:root "${tree}/etc/systemd/system.conf.d"
    sudo chmod 755 "${tree}/etc/systemd/system.conf.d"
    sudo chmod 644 "${tree}/etc/systemd/system.conf.d/fast-shutdown.conf"

    sudo rm -f \
        "${tree}/etc/systemd/system/multi-user.target.wants/systemd-networkd-wait-online.service" \
        "${tree}/etc/systemd/system/network-online.target.wants/systemd-networkd-wait-online.service"

    # --- Volume de données ---------------------------------------------------
    #
    # Le point de montage doit exister DANS l'image : systemd refuse de monter
    # sur un répertoire absent, et il est trop tôt au boot pour compter sur
    # systemd-tmpfiles pour le créer.
    sudo mkdir -p "${tree}/data"
    sudo chown root:root "${tree}/data"
    sudo chmod 755 "${tree}/data"

    # Unité de montage plutôt qu'une ligne dans /etc/fstab : réécrire un fichier
    # est idempotent, alors qu'un append dupliquerait la ligne à chaque relance
    # (l'arbre n'étant pas re-cloné, configure_rootfs repasse sur le même arbre).
    #
    # Le nom du fichier n'est pas libre : systemd exige que l'unité porte le
    # chemin de montage échappé. /data -> data.mount ; /opt/agent/data aurait
    # donné opt-agent-data.mount.
    #
    # What= pointe sur le LABEL et non sur /dev/vdb : l'ordre des périphériques
    # virtio suit l'ordre du tableau "drives" de vm-config.json, donc un
    # troisième drive inséré au mauvais endroit renommerait le volume.
    #
    # nofail  : si le drive est absent, on démarre quand même au lieu de tomber
    #           en emergency.target. Contrepartie : l'agent doit vérifier que
    #           /data est réellement monté avant d'y écrire, sinon il écrit dans
    #           le rootfs sans s'en apercevoir.
    # noatime : pas de mise à jour des dates d'accès, inutile ici et coûteux en
    #           I/O sur un volume que l'agent va solliciter en écriture.
    #
    # noexec,nosuid,nodev remplacent `defaults` (qui vaut rw,suid,dev,exec,...).
    # /data reçoit du code écrit par un LLM et des fichiers venus de l'hôte :
    # c'est la surface la moins fiable de la VM, et rien n'oblige à ce qu'elle
    # soit exécutable.
    #
    #   noexec  interdit mmap(PROT_EXEC) sur ce volume. Python importe des .py
    #           sans bit exécutable, donc les tools fonctionnent normalement ;
    #           en revanche un ELF ou une extension .so déposée là ne peut pas
    #           être chargée. C'est voulu : le code natif doit venir de l'image,
    #           jamais du canal de transfert.
    #   nosuid  neutralise les bits setuid/setgid, sans objet sur un volume de
    #           données mais gratuit.
    #   nodev   interdit les fichiers spéciaux, qui contourneraient l'isolation
    #           des uid en donnant un accès brut aux périphériques.
    sudo tee "${tree}/etc/systemd/system/data.mount" >/dev/null <<MOUNT
[Unit]
Description=Volume de donnees de l agent
DefaultDependencies=no
After=local-fs-pre.target
Before=local-fs.target

[Mount]
What=/dev/disk/by-label/${DATA_LABEL}
Where=/data
Type=ext4
Options=rw,nosuid,nodev,noexec,nofail,noatime

[Install]
WantedBy=local-fs.target
MOUNT
    sudo chown root:root "${tree}/etc/systemd/system/data.mount"
    sudo chmod 644 "${tree}/etc/systemd/system/data.mount"

    # `systemctl enable` ne sait pas travailler sur un arbre hors ligne : on
    # pose le symlink d'activation à la main, c'est exactement ce que la
    # commande aurait fait à partir de la section [Install].
    sudo mkdir -p "${tree}/etc/systemd/system/local-fs.target.wants"
    sudo ln -sf ../data.mount \
        "${tree}/etc/systemd/system/local-fs.target.wants/data.mount"

    # SendCtrlAltDel provoque un redémarrage par défaut (ctrl-alt-del.target
    # est un alias de reboot.target). Firecracker sort bien dans les deux cas,
    # mais un cycle de reboot complet est du temps perdu quand on veut juste
    # rendre la machine. On redirige donc vers poweroff.
    sudo ln -sf /lib/systemd/system/poweroff.target \
        "${tree}/etc/systemd/system/ctrl-alt-del.target"

    if sudo test -x "${tree}/usr/bin/python3"; then
        log "python3 présent dans le rootfs"
    else
        warn "python3 absent du rootfs — à installer avant d'injecter un agent Python"
    fi
}

# ----------------------------------------------------------------------------
# configure_sandbox_user
#
# Crée le compte non privilégié sous lequel execd exécutera le code reçu.
#
# POURQUOI UN COMPTE ALORS QUE ROOT EST ACCEPTABLE DANS LE GUEST
#
# Le périmètre de sécurité est la frontière de la VM, et execd tourne en root :
# ça reste vrai. Mais il n'y a aucune raison que le code écrit par un LLM
# hérite de ces droits. Sans ce compte, un tool peut lire /opt/execd, réécrire
# le service, altérer les unités systemd, ou modifier les workdirs des sessions
# concurrentes. Le drop de privilèges ne remplace pas l'isolation VM : il ajoute
# une couche dont le coût est nul.
#
# POURQUOI ÉCRIRE DIRECTEMENT DANS /etc/passwd
#
# `useradd` agirait sur le système hôte, pas sur l'arbre. L'alternative propre
# serait un chroot, disproportionné pour trois lignes. L'uid est figé (1500,
# hors de la plage attribuée dynamiquement par adduser qui commence à 1000
# mais que l'image CI n'utilise pas) pour que toutes les images soient
# identiques : un workdir créé par une VM garde un propriétaire cohérent si
# jamais son disque est relu ailleurs.
#
# /nonexistent + nologin : ce compte n'a pas vocation à ouvrir de session. Le
# champ mot de passe « ! » dans shadow verrouille toute authentification.
# ----------------------------------------------------------------------------
configure_sandbox_user() {
    local tree="$VM_TREE"

    # Idempotence par grep plutôt que par réécriture : /etc/passwd contient les
    # comptes système de l'image, qu'il n'est pas question de régénérer.
    if sudo grep -q "^${SANDBOX_USER}:" "${tree}/etc/group" 2>/dev/null; then
        log "Compte ${SANDBOX_USER} déjà présent (gid ${SANDBOX_GID})"
    else
        log "Création du compte ${SANDBOX_USER} (uid ${SANDBOX_UID})"
        printf '%s:x:%s:\n' "$SANDBOX_USER" "$SANDBOX_GID" \
            | sudo tee -a "${tree}/etc/group" >/dev/null
    fi

    if ! sudo grep -q "^${SANDBOX_USER}:" "${tree}/etc/passwd" 2>/dev/null; then
        printf '%s:x:%s:%s:execd tool runner:/nonexistent:/usr/sbin/nologin\n' \
            "$SANDBOX_USER" "$SANDBOX_UID" "$SANDBOX_GID" \
            | sudo tee -a "${tree}/etc/passwd" >/dev/null
    fi

    # Un compte présent dans passwd mais absent de shadow est traité comme
    # ayant un mot de passe vide par certains PAM : l'entrée verrouillée n'est
    # pas cosmétique.
    if ! sudo grep -q "^${SANDBOX_USER}:" "${tree}/etc/shadow" 2>/dev/null; then
        printf '%s:!:20000:0:99999:7:::\n' "$SANDBOX_USER" \
            | sudo tee -a "${tree}/etc/shadow" >/dev/null
    fi

    # tee -a préserve les modes existants, mais on les réaffirme : un fichier
    # shadow lisible par tous est le genre de régression qui passe inaperçue.
    sudo chown root:root "${tree}/etc/passwd" "${tree}/etc/group"
    sudo chmod 644 "${tree}/etc/passwd" "${tree}/etc/group"
    sudo chown root:shadow "${tree}/etc/shadow" 2>/dev/null || \
        sudo chown root:root "${tree}/etc/shadow"
    sudo chmod 640 "${tree}/etc/shadow"
}

# ----------------------------------------------------------------------------
# purge_legacy_executor
#
# Retire agent-executor.service et /opt/agent, remplaces par execd.
#
# POURQUOI LE FAIRE PLUTOT QUE LAISSER COHABITER
#
# Les deux services ecoutent sur des ports vsock differents (5000 et 5100) et
# demarreraient tous deux au boot. Le cout n'est pas la RAM, c'est l'ambiguite :
# deux chemins d'execution du code recu, deux modeles de securite, et un doute
# a chaque diagnostic sur celui qui a reellement traite une requete. L'ancien
# executeur lance d'ailleurs le code sous root par defaut, ce que execd ne fait
# plus — le laisser en place annulerait le durcissement.
#
# La suppression est ciblee : /opt/agent n'est efface que s'il contient bien
# executor.py. Un /opt/agent qui servirait a autre chose n'est pas touche.
# ----------------------------------------------------------------------------
purge_legacy_executor() {
    local tree="$VM_TREE"
    local unit="${tree}/etc/systemd/system/agent-executor.service"
    local found=0

    if sudo test -e "$unit"; then
        sudo rm -f "$unit"
        found=1
    fi

    # Le symlink d'activation vit dans multi-user.target.wants/, mais une unite
    # peut aussi avoir ete tiree par une autre cible : on balaye tous les
    # .wants plutot que le seul multi-user. -delete dispense d'une boucle, donc
    # d'un decoupage sur des noms de fichiers.
    if sudo find "${tree}/etc/systemd/system" -path '*.wants/agent-executor.service' \
        -print -quit 2>/dev/null | grep -q .; then
        sudo find "${tree}/etc/systemd/system" -path '*.wants/agent-executor.service' \
            -delete 2>/dev/null || true
        found=1
    fi

    if sudo test -f "${tree}/opt/agent/executor.py"; then
        sudo rm -rf "${tree}/opt/agent"
        found=1
    fi

    [ "$found" -eq 1 ] && log "Ancien agent-executor retire (remplace par execd)"
    return 0
}

# ----------------------------------------------------------------------------
# install_execd
#
# Dépose le service d'exécution dans /opt/execd et pose son unité systemd.
#
# ACTIVATION CONDITIONNELLE
#
# L'unité est toujours écrite, mais le symlink d'activation n'est posé que si
# le point d'entrée existe réellement dans l'arbre. Avec Restart=always, une
# unité activée dont l'ExecStart est introuvable boucle indéfiniment au boot :
# systemd relance, échoue, relance. On préfère une VM sans service à une VM qui
# noie son journal.
#
# POURQUOI Type=simple ET NON Type=notify
#
# Un readiness signal systemd n'a pas de consommateur ici : l'hôte ne peut pas
# interroger le systemd de l'invité, et aucune autre unité ne dépend de execd.
# Le seul client réel est le code hôte qui se connecte en vsock — il doit de
# toute façon porter sa propre logique de retry (paramètre `wait=`). Type=notify
# n'ajouterait donc rien, mais ferait échouer le démarrage au timeout si execd
# omettait l'appel sd_notify.
#
# POURQUOI PURGER /data/jobs AU DÉMARRAGE
#
# Un workdir n'a de sens que pendant la vie de la connexion vsock qui le
# possède. Si execd redémarre — crash, ou reboot de la VM — toutes les sessions
# sont mortes et leurs workdirs sont des résidus. Les purger au démarrage évite
# d'avoir à écrire un ramasse-miettes, et garantit qu'une VM réutilisée repart
# d'un /data propre.
# ----------------------------------------------------------------------------
install_execd() {
    local tree="$VM_TREE"
    local dest="${tree}/opt/execd"
    local unit="${tree}/etc/systemd/system/execd.service"
    local wants="${tree}/etc/systemd/system/multi-user.target.wants"

    purge_legacy_executor

    # --- Retrait explicite ---------------------------------------------------
    if [ "$NO_EXECD" -eq 1 ]; then
        log "Retrait du service execd (--no-execd)"
        sudo rm -rf "$dest" "$unit" "${wants}/execd.service"
        return 0
    fi

    # --- Copie du source -----------------------------------------------------
    #
    # La copie est intégrale à chaque passage : c'est ce qui rend la boucle
    # « j'édite execd.py sur l'hôte, je relance make_vm.sh » utilisable. Le
    # rm -rf préalable évite qu'un fichier supprimé côté source survive dans
    # l'image, cas classique d'un module renommé qui continue d'être importé.
    if [ -n "$EXECD_SRC" ]; then
        [ -d "$EXECD_SRC" ] || die "EXECD_SRC introuvable : ${EXECD_SRC} (corrige vm.env ou passe --execd)"
        [ -f "${EXECD_SRC}/${EXECD_ENTRY}" ] \
            || die "point d'entrée absent : ${EXECD_SRC}/${EXECD_ENTRY}"

        log "Installation de execd depuis ${EXECD_SRC}"

        sudo rm -rf "$dest"
        sudo mkdir -p "$dest"

        # tar plutôt que cp -r : c'est le seul moyen portable d'exclure des
        # motifs. Les .pyc issus de l'hôte seraient de toute façon ignorés
        # (magic number lié à la version de Python), mais ils gonflent l'image
        # et brouillent les diffs entre reconstructions.
        sudo tar -C "$EXECD_SRC" -cf - \
            --exclude='__pycache__' \
            --exclude='.venv' \
            --exclude='.git' \
            --exclude='.pytest_cache' \
            --exclude='.mypy_cache' \
            --exclude='.ruff_cache' \
            --exclude='*.pyc' \
            --exclude='*.egg-info' \
            . | sudo tar -C "$dest" --no-same-owner -xf -

        # Le code du service appartient à root et n'est pas modifiable par le
        # compte sandbox : un tool ne doit pas pouvoir réécrire son propre
        # exécuteur. Lecture seule pour tous, aucun bit exécutable — python3
        # est invoqué explicitement dans l'ExecStart.
        sudo chown -R root:root "$dest"
        sudo find "$dest" -type d -exec chmod 755 {} +
        sudo find "$dest" -type f -exec chmod 644 {} +

        local nfiles
        nfiles="$(sudo find "$dest" -type f | wc -l)"
        log "  ${nfiles} fichier(s) dans /opt/execd"
    elif sudo test -d "$dest"; then
        log "execd déjà présent dans l'arbre (aucune source mémorisée)"
    fi

    # --- Unité ---------------------------------------------------------------
    #
    # Requires + After sur data.mount : sans /data, execd n'a nulle part où
    # créer ses workdirs. Échouer franchement vaut mieux que démarrer et écrire
    # dans le rootfs — qui a vocation à passer en lecture seule.
    sudo tee "$unit" >/dev/null <<UNIT
[Unit]
Description=execd - service d execution de tools (vsock:${EXECD_PORT})
# Pas de After=network.target : la VM n a aucune interface reseau.
# DefaultDependencies=no evite l ordonnancement implicite derriere
# basic.target, que rien ici ne justifie et qui retarde le boot.
DefaultDependencies=no
Requires=data.mount
After=data.mount
Before=shutdown.target
Conflicts=shutdown.target

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=EXECD_PORT=${EXECD_PORT}
Environment=EXECD_JOBS_DIR=/data/jobs
Environment=EXECD_PYDEPS=/opt/pydeps
Environment=EXECD_USER=${SANDBOX_USER}
Environment=EXECD_UID=${SANDBOX_UID}
Environment=EXECD_GID=${SANDBOX_GID}

# Un workdir n a de sens que pendant la vie de sa connexion vsock. Si execd
# redemarre, toutes les sessions sont mortes et leurs workdirs sont des
# residus : les purger ici dispense d ecrire un ramasse-miettes.
ExecStartPre=/bin/rm -rf /data/jobs
ExecStartPre=/bin/mkdir -p /data/jobs
# 0711 : le compte ${SANDBOX_USER} traverse pour atteindre son workdir, sans
# pouvoir enumerer le repertoire et decouvrir les sessions voisines.
ExecStartPre=/bin/chmod 0711 /data/jobs
ExecStart=/usr/bin/python3 /opt/execd/${EXECD_ENTRY}

# L hote se contente de reconnecter : un crash isole ne doit pas condamner la
# VM. 200 ms plutot qu une seconde, la reprise est immediate.
Restart=always
RestartSec=200ms

# execd n ecrit QUE dans /data. Le reste de l arborescence, /opt/execd compris,
# est en lecture seule pour le service lui-meme : un defaut du daemon ne peut
# pas reecrire son propre code ni les unites systemd.
ProtectSystem=strict
ReadWritePaths=/data
ProtectHome=yes
# ProtectSystem=strict rend /tmp non inscriptible ; PrivateTmp redonne un /tmp
# prive et ecrivable, au cas ou la stdlib en aurait besoin.
PrivateTmp=yes
# Ne bloque pas l abandon de privileges vers ${SANDBOX_USER} : NO_NEW_PRIVS
# interdit d en GAGNER, jamais d en ceder.
NoNewPrivileges=yes

StandardOutput=journal
StandardError=journal
SyslogIdentifier=execd

[Install]
WantedBy=multi-user.target
UNIT
    sudo chown root:root "$unit"
    sudo chmod 644 "$unit"

    # --- Activation ----------------------------------------------------------
    sudo mkdir -p "$wants"
    if sudo test -f "${dest}/${EXECD_ENTRY}"; then
        sudo ln -sf ../execd.service "${wants}/execd.service"
        log "execd activé — port vsock ${EXECD_PORT}, uid ${SANDBOX_UID}"
    else
        sudo rm -f "${wants}/execd.service"
        warn "execd non activé : /opt/execd/${EXECD_ENTRY} absent de l'arbre"
        warn "  dépose le service puis relance : ./make_vm.sh ${VM_ARG} --execd <dir>"
    fi
}

# ----------------------------------------------------------------------------
# normalize_permissions
#
# Filet de sécurité sur les droits de l'arbre avant construction de l'image.
#
# POURQUOI rootfs-tree APPARTIENT À root
#
# C'est le comportement correct, pas un défaut à corriger. Un rootfs Linux
# doit appartenir à root : /etc/shadow en 0640 root:shadow, /usr/bin/sudo en
# setuid root, les nœuds de /dev avec leurs majeur/mineur. unsquashfs sous
# sudo restitue ces uid/gid ; sans sudo tout appartiendrait à l'utilisateur
# courant et le système invité démarrerait en vrac.
#
# Corollaire : toute modification de l'arbre passe par sudo. Utiliser
# `sudo tee` plutôt que `sudo cat >`, car avec une redirection c'est le shell
# non privilégié qui ouvre le fichier, pas la commande.
#
# CE QUE FAIT CETTE FONCTION
#
# 1) Vérifie que la racine de l'arbre est bien root:root — si ce n'est pas le
#    cas, l'extraction ou la copie s'est faite sans sudo et l'image sera
#    inutilisable.
# 2) Réaffirme les modes des répertoires sensibles. unsquashfs les préserve
#    déjà, mais un `cp` maladroit lors d'une injection manuelle peut les
#    casser, et le symptôme (systemd qui refuse de démarrer, sudo qui
#    proteste) n'oriente pas vers la cause.
# 3) Signale les fichiers appartenant à des uid hors plage système, typiques
#    d'un fichier copié depuis l'hôte sans chown : ils arrivent dans le guest
#    avec un uid qui n'y correspond à rien.
#
# Aucune correction destructive : on n'ajuste que ce dont la valeur correcte
# est certaine.
# ----------------------------------------------------------------------------
normalize_permissions() {
    log "Normalisation des droits"

    local tree="$VM_TREE"

    local owner
    owner="$(stat -c '%U:%G' "$tree")"
    if [ "$owner" != "root:root" ]; then
        die "rootfs-tree appartient à ${owner} au lieu de root:root — copie faite sans sudo, relance avec --reset-tree"
    fi

    sudo chmod 755 "$tree"

    # Répertoires dont le mode a une valeur canonique et dont une déviation
    # empêche le boot ou casse des composants du système invité.
    #   /tmp, /var/tmp  1777  sticky bit, sinon systemd-tmpfiles échoue
    #   /root            700  refusé en lecture par sudo/ssh si trop permissif
    #   /etc/ssh         755
    local d
    for d in tmp var/tmp; do
        [ -d "${tree}/${d}" ] && sudo chmod 1777 "${tree}/${d}"
    done
    [ -d "${tree}/root" ]    && sudo chmod 700 "${tree}/root"
    [ -d "${tree}/etc/ssh" ] && sudo chmod 755 "${tree}/etc/ssh"

    # Fichiers appartenant à un uid non système (>= 1000) et différent des
    # comptes légitimes de l'image : signature d'une copie depuis l'hôte sans
    # chown. On avertit sans corriger, la valeur attendue dépendant du fichier.
    #
    # SANDBOX_UID est exclu : c'est un compte qu'on a créé volontairement. Sans
    # cette exclusion, tout fichier lui appartenant serait signalé comme une
    # erreur de copie — un faux positif qui apprend à ignorer l'avertissement.
    local strays
    strays="$(sudo find "$tree" -uid +999 ! -uid 65534 ! -uid "$SANDBOX_UID" \
              -printf '%u %p\n' 2>/dev/null | head -5 || true)"

    if [ -n "$strays" ]; then
        warn "fichiers avec un uid non système (copiés sans chown ?) :"
        printf '%s\n' "$strays" | while read -r line; do
            warn "  ${line}"
        done
        warn "corrige avec : sudo chown root:root <fichier>"
    fi

    return 0
}

# ----------------------------------------------------------------------------
# build_ext4
#
# Construit l'image disque bootable à partir de l'arbre de la VM.
#
# L'option `-d <dir>` de mkfs.ext4 peuple le système de fichiers directement
# depuis un répertoire, SANS montage. C'est ce qui permet de tout faire sans
# `mount -o loop`, donc sans occuper de périphérique loop et sans risque de
# laisser un montage orphelin.
#
# POINT CLÉ : `-d` réalise une COPIE au moment du mkfs. Il n'existe aucune
# synchronisation ultérieure entre rootfs-tree/ et rootfs.ext4. Toute
# modification de l'arbre impose de relancer cette commande — c'est l'erreur
# la plus fréquente lors du développement de l'agent.
#
# `-b 4096` fixe la taille de bloc, qui correspond à la taille de page x86_64
# et à la granularité des I/O virtio : c'est l'alignement optimal.
#
# SUR LES DROITS — ne pas confondre deux niveaux :
#
#   rootfs-tree/  et le CONTENU de l'image  ->  root:root  (droits du système
#                                               invité, préservés tels quels)
#   rootfs.ext4  en tant que FICHIER HÔTE   ->  utilisateur courant
#
# D'où le `chown` final : mkfs sous sudo produit un fichier appartenant à
# root, alors que Firecracker tourne sous l'utilisateur courant et doit
# pouvoir écrire dans l'image (rootfs monté en lecture-écriture). Ce chown
# porte sur le fichier conteneur, il ne touche à rien de ce qu'il contient.
#
# Le mode 600 évite qu'un autre utilisateur de la machine lise ou modifie le
# disque de la VM, qui contiendra à terme le code et les secrets de l'agent.
# ----------------------------------------------------------------------------
build_ext4() {
    if [ ! -d "$VM_TREE" ]; then
        # Cas d'une VM en --slim : l'image existe, l'arbre a été supprimé.
        # Reconstruire imposerait de re-cloner, ce qui écraserait des
        # injections que l'utilisateur croit peut-être encore présentes.
        if [ -f "${VM_DIR}/rootfs.ext4" ]; then
            warn "rootfs-tree absent (VM slim) — rootfs.ext4 conservée telle quelle"
            warn "  pour repartir d'un arbre neuf : ./make_vm.sh ${VM_ARG} --reset-tree"
            return
        fi
        die "rootfs-tree absent et aucune image à conserver — relance avec --reset-tree"
    fi

    log "Construction de rootfs.ext4 (${ROOTFS_SIZE})"

    sudo mkfs.ext4 -q -F -b 4096 \
        -d "$VM_TREE" \
        "${VM_DIR}/rootfs.ext4" "$ROOTFS_SIZE"

    sudo chown "$(id -u):$(id -g)" "${VM_DIR}/rootfs.ext4"
    chmod 600 "${VM_DIR}/rootfs.ext4"

    log "Image : $(du -h "${VM_DIR}/rootfs.ext4" | cut -f1) ($(stat -c '%U:%G %a' "${VM_DIR}/rootfs.ext4"))"
}

# ----------------------------------------------------------------------------
# build_data_disk
#
# Crée le second disque, monté sur /data dans l'invité.
#
# POURQUOI UN VOLUME SÉPARÉ
#
# Séparer le code (rootfs, reconstruit à chaque itération) des données (état
# produit par l'agent) rend les deux cycles de vie indépendants : on peut
# reconstruire l'image système sans perdre ce que la VM a écrit. C'est aussi
# le découpage qui permettra plus tard de passer le rootfs en lecture seule
# avec un overlay en RAM — /data restant le seul point d'écriture persistant,
# donc le seul état à capturer dans un snapshot.
#
# NON DESTRUCTIF PAR CONSTRUCTION
#
# Contrairement à rootfs.ext4, ce fichier n'est jamais reconstruit s'il
# existe : le recréer effacerait silencieusement le travail de l'invité.
# --reset-data requis pour repartir à zéro.
#
# SUR LES OPTIONS DE MKFS
#
#   -m 0                 supprime les 5 % de blocs réservés à root. Négligeable
#                        sur 2 Go, mais c'est 10 Mo sur 200
#   -E root_owner=0:0    sans ça, mkfs attribue le répertoire racine du nouveau
#                        FS à l'uid/gid de l'appelant. Comme on tourne SANS
#                        sudo ici, /data arriverait dans le guest en 1000:1000,
#                        un uid qui ne correspond à aucun compte de l'image
#   -L                   le label est ce sur quoi data.mount s'appuie pour
#                        trouver le volume, indépendamment du nom /dev/vdX
#   -b 4096              même alignement que le rootfs (page x86_64, I/O virtio)
#
# Pas de sudo : à la différence du rootfs, il n'y a ici ni fichier spécial ni
# uid à préserver. Le fichier appartient donc directement à l'utilisateur qui
# lancera Firecracker, ce qui est exactement ce qu'il faut.
#
# Pour pré-remplir le volume, même mécanique que pour le rootfs :
#   sudo mkfs.ext4 -q -F -b 4096 -m 0 -L <label> -d data-tree data.ext4
# ----------------------------------------------------------------------------
build_data_disk() {
    if [ -f "${VM_DIR}/data.ext4" ] && [ "$RESET_DATA" -eq 1 ]; then
        log "Suppression de data.ext4 (--reset-data)"
        rm -f "${VM_DIR}/data.ext4"
    fi

    if [ -f "${VM_DIR}/data.ext4" ]; then
        log "data.ext4 déjà présent ($(du -h "${VM_DIR}/data.ext4" | cut -f1)) — conservé"
        return
    fi

    log "Création de data.ext4 (${DATA_SIZE})"

    # truncate produit un fichier creux : l'espace disque n'est consommé sur
    # l'hôte qu'au fur et à mesure des écritures de l'invité.
    truncate -s "$DATA_SIZE" "${VM_DIR}/data.ext4"

    mkfs.ext4 -q -F -b 4096 -m 0 \
        -L "$DATA_LABEL" \
        -E root_owner=0:0 \
        "${VM_DIR}/data.ext4"

    chmod 600 "${VM_DIR}/data.ext4"

    log "Volume : $(du -h --apparent-size "${VM_DIR}/data.ext4" | cut -f1) label=${DATA_LABEL}"
}

# ----------------------------------------------------------------------------
# stage_runtime
#
# Dépose dans le dossier de la VM tout ce qu'il faut pour la lancer : le VMM,
# le jailer et le noyau.
#
# C'est ce qui rend le dossier autonome. L'alternative — pointer sur le cache
# via un chemin relatif — économiserait 48 Mo par VM mais casserait dès qu'on
# déplace, archive ou déploie le dossier, et interdirait de figer une VM sur
# une version de Firecracker pendant qu'on met le cache à jour.
#
# `cp --reflink=auto` évite d'ailleurs le coût disque réel sur btrfs/XFS. Le
# kernel n'est recopié que si absent ou différent : le comparer coûte moins
# que 48 Mo d'écriture à chaque reconstruction.
# ----------------------------------------------------------------------------
stage_runtime() {
    log "Installation du runtime dans le dossier de la VM"

    # `rm` avant `install` : écraser en place un binaire en cours d'exécution
    # échoue avec ETXTBSY. Dissocier le nom de l'inode contourne le problème —
    # le processus qui tourne garde son inode, le nouveau fichier est neuf.
    rm -f "${VM_DIR}/firecracker" "${VM_DIR}/jailer"
    install -m 755 "${CACHE_DIR}/firecracker" "${VM_DIR}/firecracker"
    install -m 755 "${CACHE_DIR}/jailer"      "${VM_DIR}/jailer"

    if ! cmp -s "${CACHE_DIR}/${KERNEL_FILE}" "${VM_DIR}/${KERNEL_FILE}" 2>/dev/null; then
        cp --reflink=auto "${CACHE_DIR}/${KERNEL_FILE}" "${VM_DIR}/${KERNEL_FILE}"
        chmod 644 "${VM_DIR}/${KERNEL_FILE}"
    fi

    # Un kernel d'une génération précédente resterait sinon dans le dossier et
    # ferait douter sur celui réellement utilisé au boot.
    local f
    for f in "${VM_DIR}"/vmlinux-*; do
        [ -f "$f" ] || continue
        if [ "$(basename "$f")" != "$KERNEL_FILE" ]; then
            warn "kernel obsolète supprimé : $(basename "$f")"
            rm -f "$f"
        fi
    done
}

# ----------------------------------------------------------------------------
# prepare_runtime_dir
#
# Crée le dossier d'état volatil et vérifie qu'on tient dans sun_path.
#
# POURQUOI UN SOUS-DOSSIER
#
# Sockets, PID et logs sont produits à l'exécution, pas à la construction. Les
# isoler garde le dossier de VM archivable tel quel (`tar czf` n'embarque plus
# un socket mort), et donne à l'orchestrateur un seul endroit à purger pour
# remettre une VM à zéro sans toucher aux images.
#
# LA LIMITE DES 108 OCTETS
#
# Le champ sun_path d'un sockaddr_un fait 108 octets sur Linux, terminaison
# NUL comprise — soit 107 caractères utiles. Ce n'est pas une limite de
# Firecracker mais du noyau, et elle mord sur le chemin le plus long qu'on
# produira : runtime/v.sock_<port>, avec un port jusqu'à 5 chiffres.
#
# Le symptôme d'un dépassement est un bind qui échoue sans jamais mentionner
# la longueur du chemin, d'où ce contrôle à la construction — c'est-à-dire au
# seul moment où le nom du dossier est encore facile à changer. La marge
# devient étroite sous jailer, dont le chroot préfixe tout par
# /srv/jailer/firecracker/<id>/root/.
# ----------------------------------------------------------------------------
prepare_runtime_dir() {
    mkdir -p "${VM_DIR}/runtime"

    local longest="${VM_DIR}/runtime/v.sock_65535"
    local len=${#longest}

    if [ "$len" -gt 107 ]; then
        die "chemin de socket trop long (${len} > 107 octets) : ${longest}
     déplace la VM dans une arborescence plus courte ou raccourcis son nom"
    elif [ "$len" -gt 80 ]; then
        warn "chemin de socket à ${len}/107 octets — marge faible, le chroot du"
        warn "  jailer allongera encore ces chemins"
    fi

    # Sockets de l'ancienne disposition, à la racine du dossier.
    rm -f "${VM_DIR}/v.sock" "${VM_DIR}"/v.sock_* "${VM_DIR}/fc.sock"
}

# ----------------------------------------------------------------------------
# write_config
#
# Génère la configuration lue par Firecracker au démarrage.
#
# Ce fichier est l'équivalent statique des appels PUT sur l'API HTTP : mêmes
# structures, mêmes noms de champs. Passer de l'un à l'autre est direct, ce
# qui compte pour la suite (l'orchestrateur pilotera l'API, pas ce fichier).
#
# Les chemins sont RELATIFS au répertoire de travail du processus firecracker,
# d'où le `cd` en tête de run.sh. C'est aussi ce qui rend le dossier
# déplaçable : aucun chemin absolu n'y est inscrit.
#
# Détail des boot_args :
#   console=ttyS0   redirige la console noyau vers le port série, seul moyen
#                   de voir le boot puisqu'il n'y a ni écran ni réseau
#   reboot=k        méthode de redémarrage compatible avec le VMM
#   panic=1         redémarre 1 s après un kernel panic ; combiné à reboot=k,
#                   le processus firecracker se termine au lieu de rester
#                   bloqué — indispensable pour de l'orchestration automatisée
#   pci=off         pas de bus PCI dans une microVM (périphériques virtio-MMIO
#                   uniquement) ; désactiver l'énumération accélère le boot
#
# `smt: false` désactive l'hyperthreading côté invité : recommandé pour
# limiter l'exposition aux attaques par canal auxiliaire entre VMs.
#
# ORDRE DES DRIVES — le rootfs doit rester EN PREMIER
#
# boot_args ne contient volontairement pas de `root=` : Firecracker l'ajoute
# lui-même à partir du drive marqué `is_root_device`, sous la forme
# `root=/dev/vda`. Or les périphériques virtio-MMIO sont attachés dans l'ordre
# du tableau ci-dessous, et c'est cet ordre qui détermine le suffixe (vda,
# vdb, ...). Déplacer le rootfs ferait donc pointer la racine sur le mauvais
# disque, avec un kernel panic au boot pour seul indice.
#
# C'est aussi la raison pour laquelle data.mount cible un LABEL et non
# /dev/vdb : l'ajout d'un futur troisième drive ne doit rien casser.
#
# À noter : Firecracker ne supporte pas le hotplug de périphérique bloc. Tout
# se joue avant le boot ; le `PATCH /drives/{id}` de l'API permet seulement de
# remplacer le fichier backing d'un drive déjà déclaré.
# ----------------------------------------------------------------------------
write_config() {
    log "Écriture de vm-config.json"

    cat > "${VM_DIR}/vm-config.json" <<JSON
{
  "boot-source": {
    "kernel_image_path": "${KERNEL_FILE}",
    "boot_args": "console=ttyS0 reboot=k panic=1 pci=off"
  },
  "drives": [
    {
      "drive_id": "rootfs",
      "path_on_host": "rootfs.ext4",
      "is_root_device": true,
      "is_read_only": false
    },
    {
      "drive_id": "data",
      "path_on_host": "data.ext4",
      "is_root_device": false,
      "is_read_only": false
    }
  ],
  "machine-config": {
    "vcpu_count": ${VCPU_COUNT},
    "mem_size_mib": ${MEM_SIZE_MIB},
    "smt": false
  },
  "vsock": {
    "guest_cid": ${GUEST_CID},
    "uds_path": "runtime/v.sock"
  }
}
JSON
}

# ----------------------------------------------------------------------------
# write_run_script
#
# Produit le script de lancement, dans le dossier de la VM.
#
# Sa vraie raison d'être est le nettoyage des sockets. Firecracker crée
# `v.sock` (socket Unix de l'hôte pour le vsock) et `fc.sock` (API HTTP), mais
# ne les supprime pas toujours en sortie — notamment après un Ctrl-C ou un
# crash. Au lancement suivant, le bind échoue avec AddrInUse et le message
# n'oriente pas vers la cause. Les `v.sock_<port>` sont les sockets d'écoute
# côté hôte pour le sens guest→host, à nettoyer également.
#
# Le `cd "$(dirname "$0")"` garantit que les chemins relatifs de
# vm-config.json se résolvent correctement, quel que soit le répertoire depuis
# lequel run.sh est appelé — c'est ce qui permet de lancer la VM depuis
# n'importe où, et de déplacer le dossier sans rien réécrire.
#
# `exec` remplace le shell par le processus firecracker : un niveau de
# processus en moins, et les signaux (Ctrl-C) parviennent directement au VMM.
# ----------------------------------------------------------------------------
write_run_script() {
    log "Écriture de run.sh"

    cat > "${VM_DIR}/run.sh" <<'RUN'
#!/usr/bin/env bash
# Lance la microVM. Ctrl-C pour arrêter, ou `reboot` depuis le guest.
# Tout est local à ce dossier : aucune dépendance externe.
set -euo pipefail
cd "$(dirname "$0")"

for f in firecracker rootfs.ext4 data.ext4 vm-config.json; do
    [ -e "$f" ] || { echo "$f absent — relance make_vm.sh sur ce dossier" >&2; exit 1; }
done

mkdir -p runtime

# VERROU D'INSTANCE UNIQUE
#
# Deux VMM sur les mêmes images, c'est deux écrivains concurrents sur un même
# ext4 monté en lecture-écriture : corruption silencieuse et garantie. Le
# verrou vit ici plutôt que dans l'orchestrateur, parce que c'est le seul
# endroit par lequel TOUS les lancements passent — y compris un ./run.sh tapé
# à la main pendant qu'un agent pilote déjà la VM.
#
# `exec 9<>` ouvre le descripteur dans le shell, en lecture-écriture et SANS
# tronquer ; flock l'associe au verrou. Ne pas tronquer compte : un lancement
# refusé ne doit pas effacer le PID de celui qui tourne (`exec 9>` le vidait
# avant même de tenter le verrou).
# Comme un descripteur ouvert par redirection n'est pas close-on-exec, il
# survit à l'exec final : c'est firecracker qui tient le verrou, et le noyau
# le relâche à sa mort, quelle qu'en soit la cause. Aucun état résiduel à
# nettoyer, contrairement à un fichier PID.
command -v flock >/dev/null || { echo "flock manquant : sudo apt install -y util-linux" >&2; exit 1; }
exec 9<>runtime/vm.lock
flock -n 9 || { echo "VM déjà lancée (pid $(cat runtime/vm.lock 2>/dev/null))" >&2; exit 1; }

# Verrou pris : le fichier est à nous. `>` le réécrit en entier, par une
# ouverture à part — le verrou reste sur le descripteur 9.
# `exec` préserve le PID : celui écrit ici est bien celui de firecracker.
printf '%s\n' $$ > runtime/vm.lock

# Sockets résiduels d'un run précédent : sans ce nettoyage, Firecracker
# échoue au démarrage avec AddrInUse. Sûr ici, le verrou garantit que plus
# personne ne les utilise.
rm -f runtime/v.sock runtime/v.sock_* runtime/fc.sock
rm -f v.sock v.sock_* fc.sock   # résidus de l'ancienne disposition à plat

exec ./firecracker --api-sock runtime/fc.sock --config-file vm-config.json
RUN

    chmod +x "${VM_DIR}/run.sh"

    # Ancien nom de l'ère « tout à plat ». Le laisser traînerait deux scripts
    # de lancement dont un obsolète.
    rm -f "${VM_DIR}/run"
}

# ----------------------------------------------------------------------------
# prune_tree
#
# Mode --slim : supprime l'arbre après construction de l'image.
#
# Utile pour une VM figée qu'on va cloner ou archiver : rootfs-tree représente
# la quasi-totalité du poids du dossier alors qu'il n'intervient pas dans
# l'exécution. La contrepartie est qu'il faudra re-cloner (--reset-tree) pour
# modifier l'image, en repartant du template : les injections précédentes sont
# perdues. À réserver au moment où l'image est considérée comme définitive.
# ----------------------------------------------------------------------------
prune_tree() {
    [ "$SLIM" -eq 1 ] || return 0
    [ -d "$VM_TREE" ] || return 0

    log "Suppression de rootfs-tree (--slim)"
    sudo rm -rf "$VM_TREE"
}

# ----------------------------------------------------------------------------
# verify
#
# Contrôle a posteriori du contenu de l'image.
#
# `debugfs -R "<commande>" <image>` exécute une commande de debugfs sur un
# système de fichiers ext4 SANS le monter : ni sudo, ni périphérique loop, ni
# risque d'oublier un umount. C'est l'outil de référence pour vérifier ce
# qu'on vient de construire, et pour diagnostiquer une VM qui ne démarre pas.
#
# Le contrôle porte sur l'IMAGE et non sur l'arbre : c'est l'image qui boote,
# et inspecter l'arbre laisserait passer un mkfs oublié.
# ----------------------------------------------------------------------------
verify() {
    log "Vérification de l'image"

    local img="${VM_DIR}/rootfs.ext4"

    if debugfs -R "stat /etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf" \
        "$img" >/dev/null 2>&1; then
        log "  autologin ttyS0 : ok"
    else
        warn "  autologin ttyS0 : absent"
    fi

    if debugfs -R "ls /usr/bin" "$img" 2>/dev/null | grep -q python3; then
        log "  python3 : ok"
    else
        warn "  python3 : absent"
    fi

    # Les deux moitiés du montage de /data : l'unité doit exister ET être
    # activée. Une unité présente mais sans symlink dans local-fs.target.wants
    # ne démarre jamais, et le seul symptôme est un /data vide au boot.
    if debugfs -R "stat /etc/systemd/system/local-fs.target.wants/data.mount" \
        "$img" >/dev/null 2>&1; then
        log "  montage /data (unité activée) : ok"
    else
        warn "  montage /data : unité absente ou non activée"
    fi

    # Les options de durcissement se perdent silencieusement si data.mount est
    # régénéré depuis une version antérieure du script : rien ne casse au boot,
    # /data redevient simplement exécutable. Seul un contrôle du contenu le
    # détecte.
    local mopts
    mopts="$(debugfs -R "cat /etc/systemd/system/data.mount" "$img" 2>/dev/null \
             | sed -n 's/^Options=//p' || true)"
    case "$mopts" in
        *noexec*nofail*|*nofail*noexec*)
            log "  options /data : ok (${mopts})" ;;
        "")
            warn "  options /data : illisibles — data.mount absent ?" ;;
        *)
            warn "  options /data : « ${mopts} » — noexec manquant, image obsolète" ;;
    esac

    if debugfs -R "cat /etc/passwd" "$img" 2>/dev/null \
        | grep -q "^${SANDBOX_USER}:x:${SANDBOX_UID}:"; then
        log "  compte ${SANDBOX_USER} : ok (uid ${SANDBOX_UID})"
    else
        warn "  compte ${SANDBOX_USER} : absent — execd ne pourra pas abandonner ses droits"
    fi

    # Même logique que pour data.mount : l'unité doit exister ET être activée.
    # Une unité seule est inerte, un symlink seul empêche le boot.
    if debugfs -R "stat /opt/execd/${EXECD_ENTRY}" "$img" >/dev/null 2>&1; then
        if debugfs -R "stat /etc/systemd/system/multi-user.target.wants/execd.service" \
            "$img" >/dev/null 2>&1; then
            log "  execd : ok (port ${EXECD_PORT}, activé)"
        else
            warn "  execd : code présent mais unité non activée"
        fi
    elif [ "$NO_EXECD" -eq 1 ]; then
        log "  execd : absent (--no-execd)"
    else
        warn "  execd : /opt/execd/${EXECD_ENTRY} absent de l'image"
        warn "    ./make_vm.sh ${VM_ARG} --execd <dossier-du-service>"
    fi

    if debugfs -R "stat /etc/systemd/system/agent-executor.service" \
        "$img" >/dev/null 2>&1; then
        warn "  ancien agent-executor toujours dans l'image — deux services vsock actifs"
    fi

    if debugfs -R "stat /etc/systemd/system.conf.d/fast-shutdown.conf" \
        "$img" >/dev/null 2>&1; then
        log "  extinction rapide (5 s) : ok"
    else
        warn "  extinction rapide : absente — SendCtrlAltDel risque de dépasser le timeout"
    fi

    # Le hostname doit correspondre au dossier, sinon deux VMs se présentent
    # sous le même prompt sur leurs consoles série respectives.
    local hn
    hn="$(debugfs -R "cat /etc/hostname" "$img" 2>/dev/null | tr -d '\n' || true)"
    if [ "$hn" = "$VM_NAME" ]; then
        log "  hostname : ok (${hn})"
    else
        warn "  hostname : '${hn}' ≠ '${VM_NAME}' — image reconstruite ?"
    fi

    # Le label doit correspondre à ce que data.mount cherche dans
    # /dev/disk/by-label/ — une divergence donne un montage qui échoue au boot.
    if [ -f "${VM_DIR}/data.ext4" ]; then
        local found
        found="$(dumpe2fs -h "${VM_DIR}/data.ext4" 2>/dev/null \
                 | awk -F':' '/Filesystem volume name/ {gsub(/^[ \t]+/,"",$2); print $2}')"
        if [ "$found" = "$DATA_LABEL" ]; then
            log "  volume data.ext4 : ok (label ${found})"
        else
            warn "  volume data.ext4 : label '${found}' ≠ '${DATA_LABEL}' attendu par data.mount"
        fi
    else
        warn "  volume data.ext4 : absent"
    fi

    # L'image doit être lisible ET inscriptible par l'utilisateur qui lancera
    # Firecracker, sans quoi le boot échoue sur une erreur de backing file peu
    # explicite.
    if [ -r "$img" ] && [ -w "$img" ]; then
        log "  droits image (hôte) : ok"
    else
        warn "  droits image (hôte) : non accessible par ${USER}"
        warn "    sudo chown \$(id -u):\$(id -g) ${img}"
    fi

    # Contrôle que le contenu de l'image appartient bien à root côté invité.
    # debugfs affiche l'uid dans la sortie de stat ; sur /etc/shadow un uid
    # non nul signale un arbre extrait ou copié sans sudo.
    if debugfs -R "stat /etc/shadow" "$img" 2>/dev/null | grep -qE 'User:\s+0'; then
        log "  propriété interne root : ok"
    else
        warn "  propriété interne : /etc/shadow n'appartient pas à root — le guest risque de mal démarrer"
    fi

    # La config doit pointer sur runtime/ : une VM construite avant ce
    # changement garde un uds_path à la racine, et l'orchestrateur qui lit
    # VSOCK_UDS dans vm.env chercherait alors un socket qui n'apparaît jamais.
    if grep -q '"uds_path": *"runtime/v.sock"' "${VM_DIR}/vm-config.json"; then
        log "  socket vsock : runtime/v.sock"
    else
        warn "  vm-config.json : uds_path hors de runtime/ — config obsolète"
    fi

    # Le dossier doit être auto-suffisant : c'est l'invariant central de cette
    # organisation, autant le vérifier plutôt que de le supposer.
    local missing=""
    local f
    for f in firecracker "$KERNEL_FILE" rootfs.ext4 data.ext4 vm-config.json run.sh; do
        [ -e "${VM_DIR}/${f}" ] || missing="${missing} ${f}"
    done
    if [ -z "$missing" ]; then
        log "  dossier autonome : ok"
    else
        warn "  dossier incomplet, manque :${missing}"
    fi
}

# ----------------------------------------------------------------------------
# summary
# ----------------------------------------------------------------------------
summary() {
    local tree_state="rootfs-tree/ présent (point d'injection)"
    [ -d "$VM_TREE" ] || tree_state="rootfs-tree/ absent (mode slim)"

    local execd_state="non installé (--execd <dir> pour l'ajouter)"
    if [ -n "$EXECD_SRC" ]; then
        execd_state="port ${EXECD_PORT}, uid ${SANDBOX_UID} — source ${EXECD_SRC}"
    elif sudo test -f "${VM_TREE}/opt/execd/${EXECD_ENTRY}" 2>/dev/null; then
        execd_state="port ${EXECD_PORT}, uid ${SANDBOX_UID} — source non mémorisée"
    fi

    cat <<EOF

$(log "Terminé — VM « ${VM_NAME} »")

  Dossier VM : ${VM_DIR}
  Cache      : ${CACHE_DIR}
  Kernel     : ${KERNEL_FILE}
  Rootfs     : rootfs.ext4 (${ROOTFS_SIZE}) — ${tree_state}
  Données    : data.ext4 (${DATA_SIZE}, label ${DATA_LABEL}) sur /data
               rw,nosuid,nodev,noexec,nofail,noatime
  execd      : ${execd_state}
  vCPU/RAM   : ${VCPU_COUNT} / ${MEM_SIZE_MIB} MiB
  vsock      : CID ${GUEST_CID}
  Sockets    : ${VM_DIR}/runtime/
                 fc.sock      API HTTP de Firecracker
                 v.sock       hôte -> guest (CONNECT <port>\\n)
                 v.sock_<p>   guest -> hôte (créés à la demande)

  Lancer :
    ${VM_DIR}/run.sh

  Côté hôte, lire les chemins depuis vm.env plutôt que les reconstruire :
    . ${VM_DIR}/vm.env && echo "\$VSOCK_UDS"

  Créer une autre VM (aucun téléchargement, arbre cloné depuis le cache) :
    ./make_vm.sh agent-02

  Installer / mettre à jour le service d'exécution :
    ./make_vm.sh ${VM_ARG} --execd ../execd
    # le chemin est mémorisé : les relances suivantes recopient le source

  Injecter du code puis reconstruire l'image :
    sudo cp -r tool ${VM_DIR}/rootfs-tree/opt/agent/
    sudo chown -R root:root ${VM_DIR}/rootfs-tree/opt/agent
    ./make_vm.sh ${VM_ARG}

  Se connecter à execd depuis l'hôte (handshake Firecracker) :
    . ${VM_DIR}/vm.env
    socat - UNIX-CONNECT:\$VSOCK_UDS   # puis : CONNECT \$EXECD_PORT

  Diagnostiquer le service dans la VM (console série) :
    systemctl status execd
    journalctl -u execd -n 50

  Inspecter l'image sans la monter :
    debugfs -R "ls -l /etc/systemd/system" ${VM_DIR}/rootfs.ext4

  Repartir de zéro sur cette VM (destructif) :
    ./make_vm.sh ${VM_ARG} --reset-tree --reset-data

  Supprimer la VM :
    sudo rm -rf ${VM_DIR}

  data.ext4 n'est jamais recréé sans --reset-data : il porte l'état de l'invité.

EOF
}

# ----------------------------------------------------------------------------
# Enchaînement. L'ordre est contraint par les dépendances :
#   - resolve_vm_dir avant load_vm_env : il faut le dossier pour lire vm.env
#   - adopt_legacy_artifacts avant les fetch : sinon on retéléchargerait ce
#     qui est déjà là sous l'ancienne disposition
#   - discover_ci_prefix avant fetch_kernel/fetch_squashfs (CI_PREFIX)
#   - clone puis configure puis build : le mkfs fige le contenu de l'arbre,
#     toute configuration faite après serait absente de l'image
#   - configure_sandbox_user avant install_execd : l'unité référence l'uid du
#     compte, autant qu'il existe avant qu'on écrive l'unité
#   - install_execd avant normalize_permissions : la copie du service passe
#     ensuite sous le contrôle des droits, comme le reste de l'arbre
#   - normalize_permissions juste avant build_ext4 : les droits doivent être
#     corrects au moment exact où mkfs lit l'arbre
#   - stage_runtime et write_config après fetch_kernel : ils ont besoin de
#     KERNEL_FILE
#   - prepare_runtime_dir avant write_config : mieux vaut refuser un chemin
#     trop long avant d'écrire une config qui y référence des sockets
#   - prune_tree après build_ext4, et verify après prune : la vérification
#     porte sur l'image, elle reste valable en mode slim
#   - save_vm_env en fin de parcours, pour ne mémoriser qu'un état construit
# ----------------------------------------------------------------------------
main() {
    VM_IS_NEW=0

    parse_args "$@"
    resolve_vm_dir
    check_prereqs
    load_vm_env

    [ "$VM_IS_NEW" -eq 1 ] && log "Nouvelle VM : ${VM_NAME} (CID ${GUEST_CID})"

    adopt_legacy_artifacts
    fetch_firecracker

    # Réseau uniquement si un artefact manque : créer une VM doit rester
    # possible hors connexion dès lors que le cache est chaud.
    if [ -z "$KERNEL_FILE" ] || [ ! -f "${CACHE_DIR}/${KERNEL_FILE}" ] \
       || [ -z "$(find "$CACHE_DIR" -maxdepth 1 -name '*.squashfs' -type f)" ]; then
        discover_ci_prefix
    fi

    fetch_kernel
    fetch_squashfs
    extract_template

    clone_tree
    configure_rootfs
    configure_sandbox_user
    install_execd
    normalize_permissions
    build_ext4
    build_data_disk

    stage_runtime
    prepare_runtime_dir
    write_config
    write_run_script
    prune_tree

    save_vm_env
    verify
    summary
}

main "$@"
