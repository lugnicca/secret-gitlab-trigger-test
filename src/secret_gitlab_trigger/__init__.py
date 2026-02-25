"""Cloud Function to trigger GitLab pipelines on Secret Manager events.

This function is triggered by a Cloud Audit Log event when a secret is
created, updated, or deleted. It then triggers a GitLab pipeline with
the appropriate variables.
"""

__version__ = "1.0.3"

import json
import os
from urllib.parse import quote

import functions_framework
import requests
from google.cloud import secretmanager


def get_secret_labels(secret_name: str) -> dict:
    """Fetch labels from a Secret Manager secret."""
    client = secretmanager.SecretManagerServiceClient()
    try:
        secret = client.get_secret(request={"name": secret_name})
        return dict(secret.labels) if secret.labels else {}
    except Exception as e:
        print(f"Error fetching secret labels: {e}")
        # Re-raise so Eventarc can retry transient platform/API failures.
        raise


def check_required_labels(secret_labels: dict, required_labels: dict) -> bool:
    """Check if all required labels are present with correct values."""
    for key, value in required_labels.items():
        if secret_labels.get(key) != value:
            return False
    return True


def get_event_type(method_name: str) -> str:
    """Map Cloud Audit Log method to event type."""
    mapping = {
        "AddSecretVersion": "CREATE",
        "EnableSecretVersion": "ENABLE",
        "DisableSecretVersion": "DISABLE",
        "DestroySecretVersion": "DESTROY",
    }
    return mapping.get(method_name, "UNKNOWN")


def extract_secret_name(resource_name: str) -> str:
    """Extract the short secret name from the full resource path."""
    # Format: projects/PROJECT/secrets/SECRET_NAME/versions/VERSION
    # or: projects/PROJECT/secrets/SECRET_NAME
    parts = resource_name.split("/")
    if "secrets" in parts:
        secret_idx = parts.index("secrets")
        if secret_idx + 1 < len(parts):
            return parts[secret_idx + 1]
    return resource_name


def get_secret_resource_path(resource_name: str) -> str:
    """Extract the secret resource path (without version) from the full resource path."""
    # Format: projects/PROJECT/secrets/SECRET_NAME/versions/VERSION
    parts = resource_name.split("/")
    if "versions" in parts:
        version_idx = parts.index("versions")
        return "/".join(parts[:version_idx])
    return resource_name


def trigger_gitlab_pipeline(
    gitlab_url: str,
    project_id: str,
    trigger_token: str,
    ref: str,
    variables: dict,
) -> dict:
    """Trigger a GitLab pipeline using the trigger API."""
    encoded_project = quote(project_id, safe="")
    url = f"{gitlab_url}/api/v4/projects/{encoded_project}/trigger/pipeline"

    payload = {
        "token": trigger_token,
        "ref": ref,
    }
    # Add variables
    for key, value in variables.items():
        payload[f"variables[{key}]"] = value

    response = requests.post(url, data=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def handle_secret_event(cloud_event):
    """Handle Secret Manager events from Eventarc."""
    # Parse the CloudEvent data
    data = cloud_event.data
    proto_payload = data.get("protoPayload", {})
    method_name = proto_payload.get("methodName", "").split(".")[-1]
    resource_name = proto_payload.get("resourceName", "")
    caller_email = proto_payload.get("authenticationInfo", {}).get("principalEmail", "unknown")

    print(f"Received event: method={method_name}, resource={resource_name}")

    # Get configuration from environment
    gitlab_url = os.environ.get("GITLAB_URL", "https://gitlab.com")
    gitlab_project_id = os.environ.get("GITLAB_PROJECT_ID")
    gitlab_ref = os.environ.get("GITLAB_REF", "main")
    trigger_token = os.environ.get("GITLAB_TRIGGER_TOKEN")
    required_labels_json = os.environ.get("REQUIRED_LABELS", "{}")
    project_id = os.environ.get("GCP_PROJECT_ID")

    if not gitlab_project_id or not trigger_token:
        print("Missing required environment variables")
        return

    # Parse required labels
    try:
        required_labels = json.loads(required_labels_json)
    except json.JSONDecodeError:
        print(f"Invalid REQUIRED_LABELS JSON: {required_labels_json}")
        required_labels = {}

    # Get secret resource path and check labels
    secret_resource_path = get_secret_resource_path(resource_name)
    secret_labels = get_secret_labels(secret_resource_path)

    print(f"Secret labels: {secret_labels}, Required: {required_labels}")

    if not check_required_labels(secret_labels, required_labels):
        print(f"Secret {secret_resource_path} does not have required labels, skipping")
        return

    # Prepare variables for GitLab pipeline
    event_type = get_event_type(method_name)
    secret_name = extract_secret_name(resource_name)

    variables = {
        "SECRET_EVENT_TYPE": event_type,
        "SECRET_NAME": secret_name,
        "SECRET_RESOURCE": resource_name,
        "GCP_PROJECT_ID": project_id or "",
        "TRIGGERED_BY": caller_email,
    }

    print(f"Triggering GitLab pipeline with variables: {variables}")

    # Trigger the pipeline
    try:
        result = trigger_gitlab_pipeline(
            gitlab_url=gitlab_url,
            project_id=gitlab_project_id,
            trigger_token=trigger_token,
            ref=gitlab_ref,
            variables=variables,
        )
        print(f"Pipeline triggered successfully: {result.get('web_url', 'N/A')}")
    except requests.exceptions.RequestException as e:
        print(f"Error triggering GitLab pipeline: {e}")
        # Re-raise so Eventarc can retry transient network/API failures.
        raise


@functions_framework.cloud_event
def cloud_function(cloud_event):
    """Cloud Function entry point with decorator.

    This is the entry point to use when deploying as a Cloud Function.
    Import as: from secret_gitlab_trigger import cloud_function as handle_secret_event
    """
    return handle_secret_event(cloud_event)
