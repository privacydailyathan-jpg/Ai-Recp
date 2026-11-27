
import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  AudioLines,
  Check,
  FileText,
  Phone,
  UploadCloud,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import { useBusiness } from "../business/BusinessContext";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "../components/ui";
import { api, userFacingErrorMessage } from "../lib/api";

const MAX_DOCUMENT_SIZE = 20 * 1024 * 1024;

const formatFileSize = (bytes) => {
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const isSupportedDocument = (file) => {
  const extension = file.name.split(".").pop()?.toLowerCase();

  return ["pdf", "docx", "txt"].includes(extension);
};

const initialForm = (business) => ({
  name: business?.name ?? "",
  assistant_name: business?.name
    ? `${business.name} Receptionist`
    : "",
  greeting_message: business?.name
    ? `Thank you for calling ${business.name}. How can I help you today?`
    : "",
  industry: "",
  description: "",
  website: "",
  address: "",
  business_hours: "",
  contact_phone: "",
  contact_email: "",
  country_code: "US",
  requirements_text: "",
  fallback_instructions:
    "If you cannot answer, say so plainly and do not invent an answer.",
  selected_services: ["call_answering"],
  business_timezone:
    Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
});

function OnboardingPage() {
  const { activeBusiness } = useBusiness();

  const [form, setForm] = useState(() => initialForm(activeBusiness));
  const [setup, setSetup] = useState(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [knowledgeFile, setKnowledgeFile] = useState(null);
  const [knowledgeFileError, setKnowledgeFileError] = useState("");
  const [isDragging, setIsDragging] = useState(false);

  const fileInputRef = useRef(null);

  useEffect(() => {
    let active = true;

    setError("");
    setNotice("");
    setSetup(null);
    setKnowledgeFile(null);
    setKnowledgeFileError("");

    if (!activeBusiness) {
      setForm(initialForm(null));
      setLoading(false);

      return () => {
        active = false;
      };
    }

    setLoading(true);

    void api
      .onboarding(activeBusiness.id)
      .then((result) => {
        if (!active) return;

        setSetup(result);

        if (result.status === "failed") {
          setError(
            result.error_message ||
              "Setup failed. Please review your configuration."
          );
        }

        setForm({
          ...initialForm(activeBusiness),
          ...result,
          assistant_name:
            result.assistant_name ||
            `${result.name} Receptionist`,
          greeting_message:
            result.greeting_message ||
            `Thank you for calling ${result.name}. How can I help you today?`,
          country_code: result.country_code || "US",
          selected_services: result.selected_services?.length
            ? result.selected_services
            : ["call_answering"],
        });
      })
      .catch((cause) => {
        if (active) {
          setError(
            userFacingErrorMessage(
              cause,
              "Could not load onboarding."
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

  const update = (field) => (event) => {
    setForm((current) => ({
      ...current,
      [field]: event.target.value,
    }));
  };

  const handleKnowledgeFile = (file) => {
    setKnowledgeFileError("");

    if (!file) return;

    if (!isSupportedDocument(file)) {
      setKnowledgeFileError(
        "Please select a PDF, DOCX or TXT document."
      );
      return;
    }

    if (file.size > MAX_DOCUMENT_SIZE) {
      setKnowledgeFileError(
        "The document must be smaller than 20 MB."
      );
      return;
    }

    setKnowledgeFile(file);
  };

  const removeKnowledgeFile = () => {
    setKnowledgeFile(null);
    setKnowledgeFileError("");

    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const save = async () => {
    if (!activeBusiness) return null;

    const clean = (value) =>
      (typeof value === "string" ? value.trim() : "") || null;

    const payload = {
      name: clean(form.name) || "",
      industry: clean(form.industry),
      description: clean(form.description),
      website: clean(form.website),
      address: clean(form.address),
      business_hours: clean(form.business_hours),
      contact_phone: clean(form.contact_phone),
      contact_email: clean(form.contact_email),
      country_code: form.country_code,
      assistant_name: clean(form.assistant_name),
      greeting_message: clean(form.greeting_message),
      requirements_text: clean(form.requirements_text),
      fallback_instructions: clean(form.fallback_instructions),
      selected_services: form.selected_services,
      business_timezone: form.business_timezone,
    };

    const saved = await api.saveOnboarding(
      activeBusiness.id,
      payload
    );

    setSetup(saved);
    setForm((current) => ({
      ...current,
      ...saved,
    }));

    return saved;
  };

  const saveDraft = async (event) => {
    event.preventDefault();

    setError("");
    setNotice("");
    setBusy(true);

    try {
      await save();
      setNotice(
        "Your business and receptionist configuration were saved."
      );
    } catch (cause) {
      setError(
        userFacingErrorMessage(
          cause,
          "Could not save your onboarding."
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
    setBusy(true);

    try {
      await save();

      const result = await api.activateExistingReceptionist(
        activeBusiness.id
      );

      setSetup(result);

      setNotice(
        result?.status === "active"
          ? "Your existing Vapi receptionist is active."
          : "Activation completed. Please check the receptionist status."
      );
    } catch (cause) {
      setError(
        userFacingErrorMessage(
          cause,
          "Could not activate the existing Vapi receptionist."
        )
      );
    } finally {
      setBusy(false);
    }
  };

  if (!activeBusiness) {
    return (
      <div className="page-stack">
        <PageHeader
          title="Business onboarding"
          description="Create a business workspace before setting up a receptionist."
        />

        <Card>
          <EmptyState
            title="Create a business first"
            description="Each business has its own receptionist, phone number, and call history."
            action={
              <Link
                className="button button-primary"
                to="/businesses"
              >
                Go to businesses <ArrowRight size={15} />
              </Link>
            }
          />
        </Card>
      </div>
    );
  }

  if (loading) {
    return (
      <Card>
        <LoadingState label="Loading business setup" />
      </Card>
    );
  }

  const statusLabel =
    setup?.status === "setup_in_progress"
      ? "Setup in progress"
      : setup?.status;

  return (
    <div className="page-stack onboarding-page">
      <PageHeader
        eyebrow={activeBusiness.name}
        title="Set up your AI receptionist"
        description="Configure your business and connect your existing Vapi receptionist."
        action={
          setup?.status && (
            <StatusBadge status={statusLabel} />
          )
        }
      />

      {error && <ErrorState message={error} />}

      {notice && (
        <div className="success-banner">
          <Check size={16} /> {notice}
        </div>
      )}

      <form
        className="onboarding-form"
        onSubmit={(event) => void saveDraft(event)}
      >
        <Card className="onboarding-section">
          <div className="onboarding-step-heading">
            <span className="onboarding-step-number">1</span>
            <div>
              <h2>About your business</h2>
              <p>
                Share the details callers are most likely to ask about.
                You can leave optional information for later.
              </p>
            </div>
          </div>

          <div className="onboarding-grid">
            <Field label="Business name" id="onboarding-name">
              <input
                id="onboarding-name"
                value={form.name}
                onChange={update("name")}
                maxLength={200}
                required
              />
            </Field>

            <Field
              label="Industry or category"
              id="onboarding-industry"
            >
              <input
                id="onboarding-industry"
                value={form.industry || ""}
                onChange={update("industry")}
                maxLength={120}
                placeholder="e.g. Dental clinic"
              />
            </Field>

            <Field
              label="Business description"
              id="onboarding-description"
            >
              <textarea
                id="onboarding-description"
                value={form.description || ""}
                onChange={update("description")}
                maxLength={4000}
                placeholder="What do you offer?"
              />
            </Field>

            <Field
              label="Business country code"
              id="onboarding-country"
              hint="Enter the two-letter country code for your business."
            >
              <input
                id="onboarding-country"
                value={form.country_code}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    country_code: event.target.value.toUpperCase(),
                  }))
                }
                minLength={2}
                maxLength={2}
                pattern="[A-Za-z]{2}"
                required
              />
            </Field>
          </div>

          <details className="onboarding-optional">
            <summary>Optional details</summary>

            <div className="onboarding-grid">
              <Field
                label="Receptionist name"
                id="onboarding-assistant-name"
              >
                <input
                  id="onboarding-assistant-name"
                  value={form.assistant_name}
                  onChange={update("assistant_name")}
                  maxLength={120}
                />
              </Field>

              <Field
                label="Opening greeting"
                id="onboarding-greeting"
              >
                <textarea
                  id="onboarding-greeting"
                  value={form.greeting_message}
                  onChange={update("greeting_message")}
                  maxLength={2000}
                />
              </Field>

              <Field label="Website" id="onboarding-website">
                <input
                  id="onboarding-website"
                  type="url"
                  value={form.website || ""}
                  onChange={update("website")}
                  maxLength={2048}
                  placeholder="https://"
                />
              </Field>

              <Field
                label="Business address"
                id="onboarding-address"
              >
                <input
                  id="onboarding-address"
                  value={form.address || ""}
                  onChange={update("address")}
                  maxLength={1000}
                />
              </Field>

              <Field
                label="Business hours"
                id="onboarding-hours"
                hint="Example: Monday–Friday, 9am–5pm"
              >
                <input
                  id="onboarding-hours"
                  value={form.business_hours || ""}
                  onChange={update("business_hours")}
                  maxLength={2000}
                />
              </Field>

              <Field
                label="Contact phone"
                id="onboarding-phone"
              >
                <input
                  id="onboarding-phone"
                  type="tel"
                  value={form.contact_phone || ""}
                  onChange={update("contact_phone")}
                  maxLength={64}
                />
              </Field>

              <Field
                label="Contact email"
                id="onboarding-email"
              >
                <input
                  id="onboarding-email"
                  type="email"
                  value={form.contact_email || ""}
                  onChange={update("contact_email")}
                  maxLength={320}
                />
              </Field>

              <Field
                label="Business timezone"
                id="onboarding-timezone"
              >
                <input
                  id="onboarding-timezone"
                  value={form.business_timezone}
                  onChange={update("business_timezone")}
                  maxLength={64}
                  required
                />
              </Field>
            </div>
          </details>
        </Card>

        <Card className="onboarding-section">
          <div className="onboarding-step-heading">
            <span className="onboarding-step-number">2</span>
            <div>
              <h2>Tell us what callers need</h2>
              <p>
                Describe why you need a receptionist, what callers ask,
                and what should happen when it cannot help.
              </p>
            </div>
          </div>

          <Field
            label="Your receptionist brief"
            id="onboarding-requirements"
            hint="This becomes part of the receptionist's business-specific instructions. Do not include private customer information."
          >
            <textarea
              id="onboarding-requirements"
              value={form.requirements_text || ""}
              onChange={update("requirements_text")}
              maxLength={5000}
              rows={6}
              placeholder="For example: We miss calls after hours. Callers ask about our services and opening times. If you are unsure, explain that the team will follow up."
            />
          </Field>

          <Field
            label="If it cannot answer, what should it do?"
            id="onboarding-fallback"
          >
            <textarea
              id="onboarding-fallback"
              value={form.fallback_instructions || ""}
              onChange={update("fallback_instructions")}
              maxLength={2000}
              rows={3}
            />
          </Field>

          <div className="service-selection">
            <div className="service-card service-card-selected">
              <span className="service-icon">
                <Phone size={18} />
              </span>

              <span>
                <strong>AI call answering</strong>
                <small>
                  Answer inbound calls and respond using the business
                  details you provide.
                </small>
              </span>

              <span className="service-supported">
                <Check size={14} /> Available
              </span>
            </div>

            <p className="field-hint">
              Appointment booking, call transfer, and CRM lead capture
              are not currently supported and are not offered as active
              services.
            </p>
          </div>
        </Card>

        <Card className="onboarding-section">
          <div className="onboarding-step-heading">
            <span className="onboarding-step-number">3</span>
            <div>
              <h2>Business knowledge</h2>
              <p>
                Upload a document containing your business information.
                Your AI receptionist will use it to answer customer
                questions accurately.
              </p>
            </div>
          </div>

          <div
            className={`knowledge-upload-zone ${
              isDragging ? "knowledge-upload-dragging" : ""
            }`}
            onDragOver={(event) => {
              event.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={(event) => {
              if (
                !event.currentTarget.contains(event.relatedTarget)
              ) {
                setIsDragging(false);
              }
            }}
            onDrop={(event) => {
              event.preventDefault();
              setIsDragging(false);
              handleKnowledgeFile(event.dataTransfer.files?.[0]);
            }}
          >
            <input
              ref={fileInputRef}
              id="business-knowledge-file"
              className="sr-only"
              type="file"
              accept=".pdf,.docx,.txt"
              onChange={(event) =>
                handleKnowledgeFile(event.target.files?.[0])
              }
            />

            <label htmlFor="business-knowledge-file">
              <span className="knowledge-upload-icon">
                <UploadCloud size={25} />
              </span>

              <strong>
                Drag and drop your business document here
              </strong>

              <span className="knowledge-upload-description">
                Or click to browse files from your computer
              </span>

              <span className="knowledge-file-types">
                PDF · DOCX · TXT
              </span>
            </label>
          </div>

          {knowledgeFile && (
            <div className="knowledge-selected-file">
              <span className="knowledge-file-icon">
                <FileText size={20} />
              </span>

              <div className="knowledge-file-details">
                <strong>{knowledgeFile.name}</strong>
                <span>{formatFileSize(knowledgeFile.size)}</span>
              </div>

              <button
                type="button"
                className="icon-button"
                onClick={removeKnowledgeFile}
                aria-label="Remove selected document"
              >
                <X size={18} />
              </button>
            </div>
          )}

          {knowledgeFileError && (
            <div className="knowledge-upload-error" role="alert">
              {knowledgeFileError}
            </div>
          )}

          <div className="knowledge-upload-info">
            <strong>What should your document contain?</strong>

            <ul>
              <li>Business details and contact information</li>
              <li>Services and pricing information</li>
              <li>Opening hours and service areas</li>
              <li>Frequently asked questions</li>
              <li>Business policies and procedures</li>
            </ul>
          </div>

          <div className="knowledge-upload-note">
            <Check size={15} />
            <span>
              Your document will be reviewed and processed before its
              information is added to your receptionist's knowledge.
            </span>
          </div>
        </Card>

        {setup?.phone_number && (
          <Card className="provisioning-result">
            <div className="provisioning-result-icon">
              <AudioLines size={19} />
            </div>

            <div>
              <h2>
                {setup.status === "active"
                  ? "Receptionist is active"
                  : "Phone number assigned"}
              </h2>
              <p>{setup.phone_number}</p>
            </div>

            <StatusBadge status={statusLabel} />
          </Card>
        )}

        <div className="onboarding-actions">
          <div className="form-actions">
            <Button
              type="submit"
              variant="secondary"
              disabled={busy}
            >
              {busy ? "Saving…" : "Save configuration"}
            </Button>

            <Button
              type="button"
              onClick={() => void activateExisting()}
              disabled={busy || setup?.status === "active"}
            >
              {busy ? "Activating…" : "Activate existing Vapi"}
            </Button>
          </div>

          <p>
            This uses your existing Vapi assistant and phone number.
            It does not provision a new phone number.
          </p>
        </div>
      </form>
    </div>
  );
}

export { OnboardingPage };
