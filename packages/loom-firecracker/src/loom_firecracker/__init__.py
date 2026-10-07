# SPDX-License-Identifier: Apache-2.0
"""loom-firecracker : bac à sable microVM Firecracker pour loom.

Côté hôte seulement. ``Vm`` pilote un dossier de VM construit par
``make_vm.sh`` (démarrage par ``run.sh``, arrêt, vsock) ; ``Session`` parle à
execd, le service d'exécution qui tourne dans l'invité.
"""

from loom_firecracker.session import (
    PROTOCOL,
    ExecdError,
    Execution,
    Hello,
    Output,
    ProtocolError,
    Session,
)
from loom_firecracker.vm import Stopped, Vm, VmError, VsockRefused

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
