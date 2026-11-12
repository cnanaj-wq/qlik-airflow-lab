"""Erreurs explicites : chaque cas d'échec a son type, pour décider de réessayer ou non."""

from __future__ import annotations


class QlikError(Exception):
    """Erreur de base du lab."""


class QlikConfigError(QlikError):
    """Configuration absente ou invalide (URL du tenant, clé API)."""


class QlikApiError(QlikError):
    """Réponse HTTP inattendue de l'API Qlik."""

    def __init__(self, status: int, message: str, path: str = "") -> None:
        super().__init__(f"HTTP {status} sur {path} : {message}")
        self.status = status
        self.path = path


class QlikAuthError(QlikApiError):
    """401 / 403 : clé absente, expirée ou droits insuffisants. Ne pas réessayer."""


class QlikNotFoundError(QlikApiError):
    """404 : ressource introuvable (app, reload, fichier)."""


class QlikRedirectError(QlikApiError):
    """3xx : le client ne suit JAMAIS les redirections.

    Piège diagnostiqué : GET /api/v1/users/me répond 301 vers /api/v1/users/{id}. Beaucoup de
    clients HTTP retirent l'en-tête Authorization en suivant la redirection, d'où un faux 401.
    On préfère une erreur claire qui indique où l'API voulait nous envoyer.
    """

    def __init__(self, status: int, location: str, path: str = "") -> None:
        super().__init__(status, f"redirection refusée vers {location or '(inconnu)'}", path)
        self.location = location


class ReloadFailedError(QlikError):
    """Le reload s'est terminé en échec côté Qlik (erreur de script) : réessayer ne sert à rien."""


class ReloadTimeoutError(QlikError):
    """Le reload n'a pas atteint un état final dans le délai imparti."""


class AppResolutionError(QlikError):
    """Nom d'app introuvable ou ambigu."""
