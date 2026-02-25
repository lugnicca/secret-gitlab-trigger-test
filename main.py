"""Cloud Function entry point for local development."""

from secret_gitlab_trigger import cloud_function as handle_secret_event

__all__ = ["handle_secret_event"]
