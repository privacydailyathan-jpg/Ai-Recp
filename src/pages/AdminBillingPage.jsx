import {
  Check,
  Clock3,
  Edit3,
  Plus,
  RefreshCw,
  ShieldCheck,
  X,
  Phone,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  Button,
  ErrorState,
  Field,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "../components/ui";
import { api, userFacingErrorMessage } from "../lib/api";

const emptyPlan = {
  name: "",
  description: "",
  monthly_price: "",
  currency: "USD",
  included_minutes: "",
  max_phone_numbers: "1",
  is_active: true,
};

function formatMoney(value, currency) {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(Number(value));
  } catch {
    return `${currency} ${Number(value).toFixed(2)}`;
  }
}

function PlanFields({ form, setForm, prefix }) {
  const update = (field, value) => {
    setForm((current) => ({ ...current, [field]: value }));
  };

  return (
    <>
      <div className="admin-plan-fields">
        <Field id={`${prefix}-name`} label="Plan name">
          <input
            id={`${prefix}-name`}
            required
            maxLength={80}
            value={form.name}
            placeholder="e.g. Growth"
            onChange={(event) => update("name", event.target.value)}
          />
        </Field>

        <Field id={`${prefix}-price`} label="Monthly price">
          <input
            id={`${prefix}-price`}
            type="number"
            min="0"
            step="0.01"
            required
            value={form.monthly_price}
            placeholder="79.00"
            onChange={(event) =>
              update("monthly_price", event.target.value)
            }
          />
        </Field>

        <Field id={`${prefix}-currency`} label="Currency">
          <input
            id={`${prefix}-currency`}
            required
            minLength={3}
            maxLength={3}
            pattern="[A-Z]{3}"
            value={form.currency}
            onChange={(event) =>
              update("currency", event.target.value.toUpperCase())
            }
          />
        </Field>

        <Field id={`${prefix}-minutes`} label="Included minutes">
          <input
            id={`${prefix}-minutes`}
            type="number"
            min="0"
            required
            value={form.included_minutes}
            placeholder="1000"
            onChange={(event) =>
              update("included_minutes", event.target.value)
            }
          />
        </Field>

        <Field id={`${prefix}-phones`} label="Phone number limit">
          <input
            id={`${prefix}-phones`}
            type="number"
            min="1"
            required
            value={form.max_phone_numbers}
            onChange={(event) =>
              update("max_phone_numbers", event.target.value)
            }
          />
        </Field>
      </div>

      <Field id={`${prefix}-description`} label="Plan description">
        <textarea
          id={`${prefix}-description`}
          maxLength={2000}
          rows={3}
          value={form.description}
          placeholder="Describe what this plan offers..."
          onChange={(event) => update("description", event.target.value)}
        />
      </Field>
    </>
  );
}

function planPayload(form) {
  return {
    ...form,
    monthly_price: Number(form.monthly_price),
    included_minutes: Number(form.included_minutes),
    max_phone_numbers: Number(form.max_phone_numbers),
  };
}

function PlanCard({ plan, onEdit, onToggle, busy }) {
  return (
    <article
      className={`marketing-plan-card admin-plan-card ${
        plan.is_active ? "" : "admin-plan-inactive"
      }`}
    >
      <div className="marketing-plan-topline">
        <span className="marketing-plan-label">Pricing plan</span>
        <StatusBadge status={plan.is_active ? "active" : "disabled"} />
      </div>

      <h3>{plan.name}</h3>

      <p className="marketing-plan-description">
        {plan.description || "No description provided."}
      </p>

      <div className="marketing-plan-price">
        <strong>{formatMoney(plan.monthly_price, plan.currency)}</strong>
        <span>/ month</span>
      </div>

      <div className="marketing-plan-details">
        <div>
          <Clock3 size={17} />
          <span>
            <strong>
              {Number(plan.included_minutes).toLocaleString()}
            </strong>{" "}
            included minutes
          </span>
        </div>

        <div>
          <Phone size={17} />
          <span>
            Up to{" "}
            <strong>{Number(plan.max_phone_numbers).toLocaleString()}</strong>{" "}
            phone numbers
          </span>
        </div>
      </div>

      <ul className="marketing-plan-includes">
        <li>
          <Check size={14} />
          Inbound AI call answering
        </li>
        <li>
          <Check size={14} />
          Business-specific receptionist
        </li>
        <li>
          <Check size={14} />
          Call history and usage tracking
        </li>
      </ul>

      <div className="admin-plan-actions">
        <Button
          variant="secondary"
          size="sm"
          disabled={busy}
          onClick={() => onEdit(plan)}
        >
          <Edit3 size={14} />
          Edit
        </Button>

        <Button
          variant={plan.is_active ? "ghost" : "secondary"}
          size="sm"
          disabled={busy}
          onClick={() => onToggle(plan)}
        >
          {plan.is_active ? (
            <>
              <X size={14} />
              Deactivate
            </>
          ) : (
            <>
              <Check size={14} />
              Activate
            </>
          )}
        </Button>
      </div>
    </article>
  );
}

export function AdminBillingPage() {
  const [plans, setPlans] = useState([]);
  const [form, setForm] = useState(emptyPlan);
  const [editingPlan, setEditingPlan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await api.adminBillingPlans();
      setPlans(Array.isArray(result) ? result : []);
    } catch (cause) {
      setError(
        userFacingErrorMessage(cause, "Billing plans could not be loaded.")
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const resetForm = () => {
    setEditingPlan(null);
    setForm(emptyPlan);
  };

  const startEditing = (plan) => {
    setEditingPlan(plan);
    setForm({
      name: plan.name,
      description: plan.description || "",
      monthly_price: String(plan.monthly_price),
      currency: plan.currency,
      included_minutes: String(plan.included_minutes),
      max_phone_numbers: String(plan.max_phone_numbers),
      is_active: plan.is_active,
    });

    document
      .getElementById("admin-plan-editor")
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const submitPlan = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");

    try {
      if (editingPlan) {
        await api.updateBillingPlan(
          editingPlan.id,
          planPayload(form)
        );
        setNotice(`${editingPlan.name} updated successfully.`);
      } else {
        await api.createBillingPlan(planPayload(form));
        setNotice("New pricing plan created successfully.");
      }

      resetForm();
      await refresh();
    } catch (cause) {
      setError(
        userFacingErrorMessage(
          cause,
          editingPlan
            ? "Could not update the plan."
            : "Could not create the plan."
        )
      );
    } finally {
      setBusy(false);
    }
  };

  const togglePlan = async (plan) => {
    setBusy(true);
    setError("");
    setNotice("");

    try {
      await api.updateBillingPlan(plan.id, {
        is_active: !plan.is_active,
      });
      setNotice(
        `${plan.name} ${plan.is_active ? "deactivated" : "activated"} successfully.`
      );
      await refresh();
    } catch (cause) {
      setError(
        userFacingErrorMessage(cause, "Could not update the plan status.")
      );
    } finally {
      setBusy(false);
    }
  };

  const activeCount = plans.filter((plan) => plan.is_active).length;

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="Platform administration"
        title="Billing plans"
        description="Create pricing plans and control which plans customers can see."
        action={
          <Button
            variant="secondary"
            disabled={loading || busy}
            onClick={() => void refresh()}
          >
            <RefreshCw size={15} />
            Refresh
          </Button>
        }
      />

      {error && (
        <ErrorState message={error} onRetry={() => void refresh()} />
      )}

      {notice && (
        <div className="success-banner">
          <ShieldCheck size={16} />
          {notice}
        </div>
      )}

      {loading ? (
        <LoadingState label="Loading billing plans" />
      ) : (
        <>
          <section className="admin-billing-overview">
            <div>
              <span className="admin-overview-label">Total plans</span>
              <strong>{plans.length}</strong>
              <small>Created pricing plans</small>
            </div>

            <div>
              <span className="admin-overview-label">Published plans</span>
              <strong>{activeCount}</strong>
              <small>Visible to customers</small>
            </div>

            <div>
              <span className="admin-overview-label">Inactive plans</span>
              <strong>{plans.length - activeCount}</strong>
              <small>Hidden from customers</small>
            </div>
          </section>

          <section className="admin-billing-section">
            <div className="admin-billing-section-heading">
              <div>
                <span className="admin-section-eyebrow">Plan catalogue</span>
                <h2>Manage pricing plans</h2>
                <p>
                  Update prices, included minutes, phone limits and visibility.
                </p>
              </div>
              <span className="admin-plan-count">
                {plans.length} {plans.length === 1 ? "plan" : "plans"}
              </span>
            </div>

            {plans.length === 0 ? (
              <div className="admin-billing-empty">
                <div>
                  <Plus size={20} />
                </div>
                <h3>No pricing plans yet</h3>
                <p>Create your first plan using the form below.</p>
              </div>
            ) : (
              <div className="workspace-pricing-grid admin-pricing-grid">
                {plans.map((plan) => (
                  <PlanCard
                    key={plan.id}
                    plan={plan}
                    onEdit={startEditing}
                    onToggle={togglePlan}
                    busy={busy}
                  />
                ))}
              </div>
            )}
          </section>

          <section
            className="admin-plan-editor"
            id="admin-plan-editor"
          >
            <div className="admin-billing-section-heading">
              <div>
                <span className="admin-section-eyebrow">
                  {editingPlan ? "Update existing plan" : "New pricing plan"}
                </span>
                <h2>{editingPlan ? "Edit plan" : "Create a plan"}</h2>
                <p>
                  {editingPlan
                    ? "Make changes to this plan and save when you're ready."
                    : "Configure the price and limits for a new customer plan."}
                </p>
              </div>

              {editingPlan && (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={resetForm}
                >
                  <X size={14} />
                  Cancel editing
                </Button>
              )}
            </div>

            <form className="admin-plan-form" onSubmit={submitPlan}>
              <PlanFields
                form={form}
                setForm={setForm}
                prefix="admin-plan"
              />

              <div className="admin-plan-form-footer">
                <p>
                  {editingPlan
                    ? "Saving will update this plan's existing details."
                    : "New plans are created as active and will be visible in the customer catalogue."}
                </p>

                <Button type="submit" disabled={busy}>
                  {editingPlan ? (
                    <>
                      <Check size={15} />
                      Save changes
                    </>
                  ) : (
                    <>
                      <Plus size={15} />
                      Create plan
                    </>
                  )}
                </Button>
              </div>
            </form>
          </section>
        </>
      )}
    </div>
  );
}
