# SPDX-License-Identifier: Apache-2.0
"""Multi-clients : contexte, surcharges, secrets, isolation, budgets (fonctions L1 à L3).

- ``registry`` : les clients déclarés (``tenants``) et leurs surcharges ;
- ``secrets`` : table des secrets de chaque client ;
- ``router`` : journal et magasin de fichiers propres à un client ;
- ``quota`` : runs par minute d'un client, débit d'une clé d'API ;
- ``usage`` et ``period`` : budgets par client et par jour ou par mois.
"""

from loom_ia.tenancy.period import PERIODS, Period
from loom_ia.tenancy.quota import Quota, QuotaExceeded, RateWindow
from loom_ia.tenancy.registry import Tenant, Tenants, UnknownTenant
from loom_ia.tenancy.router import RoutedArtifactStore, RoutedEventStore, TenantRouter
from loom_ia.tenancy.secrets import EnvironmentSecrets
from loom_ia.tenancy.usage import (
    BudgetExhausted,
    Exceeded,
    TenantConsumption,
    TenantUsage,
)

__all__ = [
    "PERIODS",
    "BudgetExhausted",
    "EnvironmentSecrets",
    "Exceeded",
    "Period",
    "Quota",
    "QuotaExceeded",
    "RateWindow",
    "RoutedArtifactStore",
    "RoutedEventStore",
    "Tenant",
    "TenantConsumption",
    "TenantRouter",
    "TenantUsage",
    "Tenants",
    "UnknownTenant",
]
