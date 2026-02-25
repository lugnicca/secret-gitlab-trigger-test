"""Unit tests for the secret-gitlab-trigger Cloud Function."""

import os
from unittest.mock import Mock, patch

import pytest
import requests

from secret_gitlab_trigger import (
    check_required_labels,
    extract_secret_name,
    get_event_type,
    get_secret_labels,
    get_secret_resource_path,
    handle_secret_event,
    trigger_gitlab_pipeline,
)


class TestGetEventType:
    """Tests for get_event_type function."""

    def test_add_secret_version(self):
        assert get_event_type("AddSecretVersion") == "CREATE"

    def test_enable_secret_version(self):
        assert get_event_type("EnableSecretVersion") == "ENABLE"

    def test_disable_secret_version(self):
        assert get_event_type("DisableSecretVersion") == "DISABLE"

    def test_destroy_secret_version(self):
        assert get_event_type("DestroySecretVersion") == "DESTROY"

    def test_unknown_method(self):
        assert get_event_type("UnknownMethod") == "UNKNOWN"

    def test_empty_string(self):
        assert get_event_type("") == "UNKNOWN"


class TestExtractSecretName:
    """Tests for extract_secret_name function."""

    def test_full_resource_path_with_version(self):
        resource = "projects/my-project/secrets/my-secret/versions/1"
        assert extract_secret_name(resource) == "my-secret"

    def test_resource_path_without_version(self):
        resource = "projects/my-project/secrets/my-secret"
        assert extract_secret_name(resource) == "my-secret"

    def test_secret_with_dashes(self):
        resource = "projects/my-project/secrets/my-complex-secret-name/versions/latest"
        assert extract_secret_name(resource) == "my-complex-secret-name"

    def test_secret_with_underscores(self):
        resource = "projects/my-project/secrets/my_secret_name/versions/2"
        assert extract_secret_name(resource) == "my_secret_name"

    def test_malformed_path_no_secrets(self):
        resource = "projects/my-project/other/something"
        assert extract_secret_name(resource) == resource

    def test_empty_string(self):
        assert extract_secret_name("") == ""


class TestGetSecretResourcePath:
    """Tests for get_secret_resource_path function."""

    def test_path_with_version(self):
        resource = "projects/my-project/secrets/my-secret/versions/1"
        expected = "projects/my-project/secrets/my-secret"
        assert get_secret_resource_path(resource) == expected

    def test_path_with_latest_version(self):
        resource = "projects/my-project/secrets/my-secret/versions/latest"
        expected = "projects/my-project/secrets/my-secret"
        assert get_secret_resource_path(resource) == expected

    def test_path_without_version(self):
        resource = "projects/my-project/secrets/my-secret"
        assert get_secret_resource_path(resource) == resource

    def test_empty_string(self):
        assert get_secret_resource_path("") == ""


class TestCheckRequiredLabels:
    """Tests for check_required_labels function."""

    def test_all_labels_match(self):
        secret_labels = {"application": "n8n", "environment": "prod"}
        required_labels = {"application": "n8n"}
        assert check_required_labels(secret_labels, required_labels) is True

    def test_exact_match(self):
        secret_labels = {"application": "n8n"}
        required_labels = {"application": "n8n"}
        assert check_required_labels(secret_labels, required_labels) is True

    def test_missing_required_label(self):
        secret_labels = {"environment": "prod"}
        required_labels = {"application": "n8n"}
        assert check_required_labels(secret_labels, required_labels) is False

    def test_wrong_label_value(self):
        secret_labels = {"application": "other-app"}
        required_labels = {"application": "n8n"}
        assert check_required_labels(secret_labels, required_labels) is False

    def test_multiple_required_labels_all_match(self):
        secret_labels = {"application": "n8n", "environment": "prod", "team": "dsi"}
        required_labels = {"application": "n8n", "environment": "prod"}
        assert check_required_labels(secret_labels, required_labels) is True

    def test_multiple_required_labels_one_missing(self):
        secret_labels = {"application": "n8n"}
        required_labels = {"application": "n8n", "environment": "prod"}
        assert check_required_labels(secret_labels, required_labels) is False

    def test_empty_required_labels(self):
        secret_labels = {"application": "n8n"}
        required_labels = {}
        assert check_required_labels(secret_labels, required_labels) is True

    def test_empty_secret_labels(self):
        secret_labels = {}
        required_labels = {"application": "n8n"}
        assert check_required_labels(secret_labels, required_labels) is False

    def test_both_empty(self):
        assert check_required_labels({}, {}) is True


class TestGetSecretLabels:
    """Tests for get_secret_labels function."""

    @patch("secret_gitlab_trigger.secretmanager.SecretManagerServiceClient")
    def test_returns_labels(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        mock_secret = Mock()
        mock_secret.labels = {"application": "n8n", "environment": "prod"}
        mock_client.get_secret.return_value = mock_secret

        result = get_secret_labels("projects/my-project/secrets/my-secret")

        assert result == {"application": "n8n", "environment": "prod"}
        mock_client.get_secret.assert_called_once_with(
            request={"name": "projects/my-project/secrets/my-secret"}
        )

    @patch("secret_gitlab_trigger.secretmanager.SecretManagerServiceClient")
    def test_returns_empty_dict_when_no_labels(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        mock_secret = Mock()
        mock_secret.labels = None
        mock_client.get_secret.return_value = mock_secret

        result = get_secret_labels("projects/my-project/secrets/my-secret")

        assert result == {}

    @patch("secret_gitlab_trigger.secretmanager.SecretManagerServiceClient")
    def test_raises_on_exception(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        mock_client.get_secret.side_effect = Exception("Secret not found")

        with pytest.raises(Exception, match="Secret not found"):
            get_secret_labels("projects/my-project/secrets/nonexistent")


class TestTriggerGitlabPipeline:
    """Tests for trigger_gitlab_pipeline function."""

    @patch("secret_gitlab_trigger.requests.post")
    def test_successful_trigger(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": 123,
            "web_url": "https://gitlab.com/group/project/-/pipelines/123",
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        result = trigger_gitlab_pipeline(
            gitlab_url="https://gitlab.com",
            project_id="group/project",
            trigger_token="test-token",
            ref="main",
            variables={"SECRET_NAME": "my-secret"},
        )

        assert result["id"] == 123
        assert "web_url" in result

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert (
            call_args[0][0] == "https://gitlab.com/api/v4/projects/group%2Fproject/trigger/pipeline"
        )
        assert call_args[1]["data"]["token"] == "test-token"
        assert call_args[1]["data"]["ref"] == "main"
        assert call_args[1]["data"]["variables[SECRET_NAME]"] == "my-secret"

    @patch("secret_gitlab_trigger.requests.post")
    def test_project_id_encoding(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {"id": 1}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        trigger_gitlab_pipeline(
            gitlab_url="https://gitlab.com",
            project_id="group/subgroup/project",
            trigger_token="token",
            ref="main",
            variables={},
        )

        call_url = mock_post.call_args[0][0]
        assert "group%2Fsubgroup%2Fproject" in call_url

    @patch("secret_gitlab_trigger.requests.post")
    def test_multiple_variables(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {"id": 1}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        trigger_gitlab_pipeline(
            gitlab_url="https://gitlab.com",
            project_id="group/project",
            trigger_token="token",
            ref="main",
            variables={"VAR1": "value1", "VAR2": "value2", "VAR3": "value3"},
        )

        call_data = mock_post.call_args[1]["data"]
        assert call_data["variables[VAR1]"] == "value1"
        assert call_data["variables[VAR2]"] == "value2"
        assert call_data["variables[VAR3]"] == "value3"


class TestHandleSecretEvent:
    """Tests for the main handle_secret_event cloud function."""

    def create_cloud_event(self, method_name, resource_name, principal_email="user@example.com"):
        """Helper to create a mock CloudEvent."""
        cloud_event = Mock()
        cloud_event.data = {
            "protoPayload": {
                "methodName": f"google.cloud.secretmanager.v1.SecretManagerService.{method_name}",
                "resourceName": resource_name,
                "authenticationInfo": {"principalEmail": principal_email},
            }
        }
        return cloud_event

    @patch.dict(
        os.environ,
        {
            "GITLAB_URL": "https://gitlab.com",
            "GITLAB_PROJECT_ID": "group/project",
            "GITLAB_REF": "main",
            "GITLAB_TRIGGER_TOKEN": "gitlab-token",
            "REQUIRED_LABELS": '{"application": "n8n"}',
            "GCP_PROJECT_ID": "my-project",
        },
    )
    @patch("secret_gitlab_trigger.trigger_gitlab_pipeline")
    @patch("secret_gitlab_trigger.get_secret_labels")
    def test_successful_trigger(self, mock_get_labels, mock_trigger):
        mock_get_labels.return_value = {"application": "n8n"}
        mock_trigger.return_value = {"web_url": "https://gitlab.com/pipelines/1"}

        cloud_event = self.create_cloud_event(
            "AddSecretVersion", "projects/my-project/secrets/my-secret/versions/1"
        )

        handle_secret_event(cloud_event)

        mock_trigger.assert_called_once()
        call_kwargs = mock_trigger.call_args[1]
        assert call_kwargs["gitlab_url"] == "https://gitlab.com"
        assert call_kwargs["project_id"] == "group/project"
        assert call_kwargs["trigger_token"] == "gitlab-token"
        assert call_kwargs["ref"] == "main"
        assert call_kwargs["variables"]["SECRET_EVENT_TYPE"] == "CREATE"
        assert call_kwargs["variables"]["SECRET_NAME"] == "my-secret"

    @patch.dict(
        os.environ,
        {
            "GITLAB_URL": "https://gitlab.com",
            "GITLAB_PROJECT_ID": "group/project",
            "GITLAB_REF": "main",
            "GITLAB_TRIGGER_TOKEN": "gitlab-token",
            "REQUIRED_LABELS": '{"application": "n8n"}',
            "GCP_PROJECT_ID": "my-project",
        },
    )
    @patch("secret_gitlab_trigger.trigger_gitlab_pipeline")
    @patch("secret_gitlab_trigger.get_secret_labels")
    def test_skips_when_labels_dont_match(self, mock_get_labels, mock_trigger):
        mock_get_labels.return_value = {"application": "other-app"}

        cloud_event = self.create_cloud_event(
            "AddSecretVersion", "projects/my-project/secrets/my-secret/versions/1"
        )

        handle_secret_event(cloud_event)

        mock_trigger.assert_not_called()

    @patch.dict(
        os.environ,
        {
            "GITLAB_URL": "https://gitlab.com",
            "GITLAB_PROJECT_ID": "group/project",
            "GITLAB_REF": "main",
            "GITLAB_TRIGGER_TOKEN": "gitlab-token",
            "REQUIRED_LABELS": '{"application": "n8n"}',
            "GCP_PROJECT_ID": "my-project",
        },
    )
    @patch("secret_gitlab_trigger.trigger_gitlab_pipeline")
    @patch("secret_gitlab_trigger.get_secret_labels")
    def test_skips_when_no_labels(self, mock_get_labels, mock_trigger):
        mock_get_labels.return_value = {}

        cloud_event = self.create_cloud_event(
            "AddSecretVersion", "projects/my-project/secrets/my-secret/versions/1"
        )

        handle_secret_event(cloud_event)

        mock_trigger.assert_not_called()

    @patch.dict(os.environ, {}, clear=True)
    @patch("secret_gitlab_trigger.trigger_gitlab_pipeline")
    def test_returns_early_when_missing_env_vars(self, mock_trigger):
        cloud_event = self.create_cloud_event(
            "AddSecretVersion", "projects/my-project/secrets/my-secret/versions/1"
        )

        handle_secret_event(cloud_event)

        mock_trigger.assert_not_called()

    @patch.dict(
        os.environ,
        {
            "GITLAB_PROJECT_ID": "group/project",
            "GITLAB_TRIGGER_TOKEN": "token",
            "REQUIRED_LABELS": "invalid-json",
            "GCP_PROJECT_ID": "my-project",
        },
    )
    @patch("secret_gitlab_trigger.trigger_gitlab_pipeline")
    @patch("secret_gitlab_trigger.get_secret_labels")
    def test_handles_invalid_required_labels_json(self, mock_get_labels, mock_trigger):
        # With invalid JSON, required_labels becomes {}, so any secret matches
        mock_get_labels.return_value = {"some": "label"}
        mock_trigger.return_value = {"web_url": "url"}

        cloud_event = self.create_cloud_event(
            "AddSecretVersion", "projects/my-project/secrets/my-secret/versions/1"
        )

        handle_secret_event(cloud_event)

        # With empty required_labels, any secret matches
        mock_trigger.assert_called_once()

    @patch.dict(
        os.environ,
        {
            "GITLAB_PROJECT_ID": "group/project",
            "GITLAB_TRIGGER_TOKEN": "token",
            "REQUIRED_LABELS": '{"application": "n8n"}',
            "GCP_PROJECT_ID": "my-project",
        },
    )
    @patch("secret_gitlab_trigger.requests.post")
    @patch("secret_gitlab_trigger.get_secret_labels")
    def test_handles_gitlab_api_error(self, mock_get_labels, mock_post):
        mock_get_labels.return_value = {"application": "n8n"}
        mock_post.side_effect = requests.exceptions.RequestException("API error")

        cloud_event = self.create_cloud_event(
            "AddSecretVersion", "projects/my-project/secrets/my-secret/versions/1"
        )

        with pytest.raises(requests.exceptions.RequestException, match="API error"):
            handle_secret_event(cloud_event)

    @patch.dict(
        os.environ,
        {
            "GITLAB_PROJECT_ID": "group/project",
            "GITLAB_TRIGGER_TOKEN": "token",
            "REQUIRED_LABELS": "{}",
            "GCP_PROJECT_ID": "my-project",
        },
    )
    @patch("secret_gitlab_trigger.trigger_gitlab_pipeline")
    @patch("secret_gitlab_trigger.get_secret_labels")
    def test_all_event_types(self, mock_get_labels, mock_trigger):
        mock_get_labels.return_value = {}
        mock_trigger.return_value = {"web_url": "url"}

        event_types = [
            ("AddSecretVersion", "CREATE"),
            ("EnableSecretVersion", "ENABLE"),
            ("DisableSecretVersion", "DISABLE"),
            ("DestroySecretVersion", "DESTROY"),
        ]

        for method_name, expected_event_type in event_types:
            mock_trigger.reset_mock()

            cloud_event = self.create_cloud_event(
                method_name, "projects/my-project/secrets/my-secret/versions/1"
            )

            handle_secret_event(cloud_event)

            assert mock_trigger.called, f"Pipeline should be triggered for {method_name}"
            call_kwargs = mock_trigger.call_args[1]
            assert call_kwargs["variables"]["SECRET_EVENT_TYPE"] == expected_event_type

    @patch.dict(
        os.environ,
        {
            "GITLAB_PROJECT_ID": "group/project",
            # Missing GITLAB_TRIGGER_TOKEN
            "REQUIRED_LABELS": "{}",
            "GCP_PROJECT_ID": "my-project",
        },
    )
    @patch("secret_gitlab_trigger.trigger_gitlab_pipeline")
    def test_returns_early_when_missing_token(self, mock_trigger):
        cloud_event = self.create_cloud_event(
            "AddSecretVersion", "projects/my-project/secrets/my-secret/versions/1"
        )

        handle_secret_event(cloud_event)

        mock_trigger.assert_not_called()

    @patch.dict(
        os.environ,
        {
            # Missing GITLAB_PROJECT_ID
            "GITLAB_TRIGGER_TOKEN": "token",
            "REQUIRED_LABELS": "{}",
            "GCP_PROJECT_ID": "my-project",
        },
    )
    @patch("secret_gitlab_trigger.trigger_gitlab_pipeline")
    def test_returns_early_when_missing_project_id(self, mock_trigger):
        cloud_event = self.create_cloud_event(
            "AddSecretVersion", "projects/my-project/secrets/my-secret/versions/1"
        )

        handle_secret_event(cloud_event)

        mock_trigger.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
