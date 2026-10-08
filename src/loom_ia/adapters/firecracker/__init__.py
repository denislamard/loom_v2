# SPDX-License-Identifier: Apache-2.0
"""Bac à sable microVM Firecracker (J6.4c), côté hôte.

``Vm`` pilote un dossier de VM construit par ``firecracker/make_vm.sh``
(démarrage par ``run.sh``, arrêt, vsock) ; ``Session`` parle à execd, le
service d'exécution qui tourne dans l'invité (``firecracker/service/``) ;
``forge`` est la source d'outils qu'un agent référence par le point d'entrée
``forge``. Aucun extra : rien d'autre que le noyau, mais Linux seulement, et
le montage n'importe ce module que si la config déclare la source.
"""

from loom_ia.adapters.firecracker.session import (
    PROTOCOL,
    ExecdError,
    Execution,
    Hello,
    Output,
    ProtocolError,
    Session,
)
from loom_ia.adapters.firecracker.vm import Stopped, Vm, VmError, VsockRefused

__all__ = [
    "PROTOCOL",
    "ExecdError",
    "Execution",
    "Hello",
    "Output",
    "ProtocolError",
    "Session",
    "Stopped",
    "Vm",
    "VmError",
    "VsockRefused",
]
