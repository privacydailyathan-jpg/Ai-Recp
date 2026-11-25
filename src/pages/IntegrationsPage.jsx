
import { Fragment, jsx, jsxs } from "react/jsx-runtime";
import {
  Activity,
  Check,
  CircleHelp,
  Copy,
  ExternalLink,
  Link2,
  Phone,
  Save,
  Shield
} from "lucide-react";
import { useEffect, useState } from "react";
import { useBusiness } from "../business/BusinessContext";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  LoadingState,
  PageHeader,
  StatusBadge
} from "../components/ui";
import {
  ApiError,
  api,
  apiBaseUrl,
  userFacingErrorMessage
} from "../lib/api";

function IntegrationsPage() {
  const { activeBusiness } = useBusiness();

  const [configuration, setConfiguration] = useState(null);
  const [notConfigured, setNotConfigured] = useState(false);

  const [assistantId, setAssistantId] = useState("");
  const [phoneNumberId, setPhoneNumberId] = useState("");
  const [isEnabled, setIsEnabled] = useState(true);

  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [activating, setActivating] = useState(false);

  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const webhookUrl = `${apiBaseUrl()}/integrations/vapi/webhook`;

  useEffect(() => {
    let active = true;

    setError("");
    setNotice("");
    setConfiguration(null);
    setNotConfigured(false);

    setAssistantId("");
    setPhoneNumberId("");
    setIsEnabled(true);

    if (!activeBusiness) {
      setLoading(false);

      return () => {
        active = false;
      };
    }

    setLoading(true);

    void api.vapi(activeBusiness.id)
      .then((result) => {
        if (!active) return;

        setConfiguration(result);
        setAssistantId(result.assistant_id);
        setPhoneNumberId(result.phone_number_id ?? "");
        setIsEnabled(result.is_enabled);
      })
      .catch((cause) => {
        if (!active) return;

        if (cause instanceof ApiError && cause.status === 404) {
          setNotConfigured(true);
        } else {
          setError(
            userFacingErrorMessage(
              cause,
              "Could not load Vapi configuration."
            )
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [activeBusiness]);

  const save = async (event) => {
    event.preventDefault();

    if (!activeBusiness) return;

    setError("");
    setNotice("");
    setBusy(true);

    try {
      const payload = {
        assistant_id: assistantId.trim(),
        phone_number_id: phoneNumberId.trim() || null,
        is_enabled: isEnabled
      };

      const saved = configuration
        ? await api.updateVapi(activeBusiness.id, payload)
        : await api.createVapi(activeBusiness.id, {
            assistant_id: payload.assistant_id,
            phone_number_id: payload.phone_number_id
          });

      setConfiguration(saved);
      setNotConfigured(false);
      setAssistantId(saved.assistant_id);
      setPhoneNumberId(saved.phone_number_id ?? "");
      setIsEnabled(saved.is_enabled);

      setNotice("Vapi configuration saved.");
    } catch (cause) {
      setError(
        userFacingErrorMessage(
          cause,
          "Could not save Vapi configuration."
        )
      );
    } finally {
      setBusy(false);
    }
  };

  const activateExisting = async () => {
    if (!activeBusiness) return;

    setError("");
    setNotice("");
    setActivating(true);

    try {
      await api.activateExistingReceptionist(activeBusiness.id);

      setNotice("Existing Vapi receptionist activated successfully.");
    } catch (cause) {
      setError(
        userFacingErrorMessage(
          cause,
          "Could not activate the existing Vapi receptionist."
        )
      );
    } finally {
      setActivating(false);
    }
  };

  const copyWebhook = async () => {
    try {
      await navigator.clipboard.writeText(webhookUrl);

      setNotice("Webhook URL copied.");
      setError("");
    } catch {
      setError(
        "Could not copy the webhook URL. Select and copy it manually."
      );
    }
  };

  const mappingIsSaved =
    configuration &&
    configuration.is_enabled &&
    configuration.assistant_id === assistantId.trim() &&
    (configuration.phone_number_id ?? "") === phoneNumberId.trim();

  if (!activeBusiness) {
    return (
      <div className="page-stack">
        <PageHeader
          title="Integrations"
          description="Connect Vapi to your receptionist."
        />

        <Card>
          <EmptyState
            title="Create a business first"
            description="Integrations are configured per business workspace."
          />
        </Card>
      </div>
    );
  }

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow={activeBusiness.name}
        title="Integrations"
        description="Connect your existing Vapi assistant and phone number to this workspace."
      />

      {error && <ErrorState message={error} />}

      {notice && (
        <div className="success-banner">
          <Check size={16} />
          {" "}
          {notice}
        </div>
      )}

      {loading ? (
        <Card>
          <LoadingState label="Loading integrations" />
        </Card>
      ) : (
        <Fragment>
          <Card className="integration-card">
            <div className="integration-card-head">
              <span className="integration-logo">V</span>

              <div className="integration-title">
                <div className="integration-name-line">
                  <h2>Vapi</h2>

                  {configuration && (
                    <StatusBadge
                      status={configuration.is_enabled ? "active" : "disabled"}
                    />
                  )}
                </div>

                <p>AI voice infrastructure</p>
              </div>

              {!notConfigured && configuration && (
                <span className="connected-label">
                  <span />
                  Configured
                </span>
              )}
            </div>

            <div className="integration-divider" />

            <div className="integration-description">
              <div>
                <h3>Assistant mapping</h3>
                <p>
                  Link an assistant and optional phone number ID.
                  This app does not provision numbers or create assistants.
                </p>
              </div>

              {configuration && (
                <label className="toggle-label">
                  <span>{isEnabled ? "Enabled" : "Disabled"}</span>

                  <input
                    type="checkbox"
                    checked={isEnabled}
                    onChange={(event) => setIsEnabled(event.target.checked)}
                    aria-label="Enable Vapi integration"
                  />

                  <span className="toggle-track" />
                </label>
              )}
            </div>

            <form
              className="integration-form"
              onSubmit={(event) => void save(event)}
            >
              <Field
                label="Vapi assistant ID"
                id="assistant-id"
                hint="The ID of an existing assistant in your Vapi account."
              >
                <input
                  id="assistant-id"
                  value={assistantId}
                  onChange={(event) => setAssistantId(event.target.value)}
                  required
                  minLength={1}
                  maxLength={255}
                  placeholder="e.g. assistant_..."
                />
              </Field>

              <Field
                label="Phone number ID"
                id="phone-number-id"
                hint="Map a phone number already configured in Vapi."
              >
                <input
                  id="phone-number-id"
                  value={phoneNumberId}
                  onChange={(event) => setPhoneNumberId(event.target.value)}
                  maxLength={255}
                  placeholder="e.g. number_..."
                />
              </Field>

              <div className="form-actions">
                <Button
                  type="submit"
                  disabled={busy || activating || !assistantId.trim()}
                >
                  <Save size={15} />
                  {busy
                    ? "Saving..."
                    : configuration
                      ? "Save mapping"
                      : "Connect Vapi"}
                </Button>
              </div>
            </form>

            {configuration && (
              <div className="form-actions">
                <Button
                  type="button"
                  disabled={
                    busy ||
                    activating ||
                    !mappingIsSaved ||
                    !phoneNumberId.trim()
                  }
                  onClick={() => void activateExisting()}
                >
                  <Activity size={15} />
                  {activating
                    ? "Activating..."
                    : "Activate existing Vapi"}
                </Button>
              </div>
            )}
          </Card>

          <div className="integration-lower-grid">
            <Card className="webhook-card">
              <div className="card-heading">
                <div>
                  <h2>Webhook endpoint</h2>
                  <p>Set this URL in your Vapi Server URL configuration.</p>
                </div>

                <span className="section-icon">
                  <Link2 size={17} />
                </span>
              </div>

              <label className="field">
                <span>Webhook URL</span>

                <span className="copy-input">
                  <input
                    readOnly
                    value={webhookUrl}
                    aria-label="Vapi webhook URL"
                  />

                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => void copyWebhook()}
                    aria-label="Copy webhook URL"
                  >
                    <Copy size={15} />
                  </Button>
                </span>
              </label>

              <div className="webhook-note">
                <Shield size={16} />
                <span>
                  Webhook authentication is managed by the backend.
                  Do not enter API keys or secrets here.
                </span>
              </div>

              <a
                className="docs-link"
                href="https://docs.vapi.ai/server-url/server-authentication"
                target="_blank"
                rel="noreferrer"
              >
                Vapi authentication setup
                {" "}
                <ExternalLink size={14} />
              </a>
            </Card>

            <Card className="integration-state-card">
              <span className="integration-state-icon">
                <Activity size={18} />
              </span>

              <h3>
                {configuration
                  ? "Configuration saved"
                  : "Connection not verified"}
              </h3>

              <p>
                {configuration
                  ? "Assistant and phone number identifiers are mapped. The backend does not currently verify the Vapi API connection."
                  : "Save the assistant mapping to configure this workspace. The backend currently has no connection-test operation."}
              </p>

              <div className="integration-fact">
                <span>
                  <Phone size={14} />
                  {" "}
                  Phone provisioning
                </span>

                <strong>Not available</strong>
              </div>

              <div className="integration-fact">
                <span>
                  <CircleHelp size={14} />
                  {" "}
                  API status check
                </span>

                <strong>Not available</strong>
              </div>
            </Card>
          </div>

          <div className="integration-footer-note">
            <CircleHelp size={15} />
            {" "}
            Attach a Vapi Custom Credential with an Authorization Bearer
            prefix to the effective Server URL configuration. The backend
            expects the matching VAPI_WEBHOOK_SECRET.
          </div>
        </Fragment>
      )}
    </div>
  );
}

export {
  IntegrationsPage
};
