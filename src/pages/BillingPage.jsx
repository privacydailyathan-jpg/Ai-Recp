import { Check, Clock3, Phone, RefreshCw, Sparkles } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button, EmptyState, ErrorState, LoadingState, PageHeader } from "../components/ui";
import { api, userFacingErrorMessage } from "../lib/api";

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

export function BillingPage() {
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await api.billingPlans();
      setPlans(result);
    } catch (cause) {
      setError(
        userFacingErrorMessage(
          cause,
          "Available plans could not be loaded."
        )
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="Workspace"
        title="Plans & pricing"
        description="Choose the right level of AI call handling for your business."
        action={
          <Button
            variant="secondary"
            disabled={loading}
            onClick={() => void refresh()}
          >
            <RefreshCw size={15} />
            Refresh
          </Button>
        }
      />

      {!loading && plans.length > 0 && (
        <div className="workspace-pricing-notice">
          <span className="workspace-pricing-notice-icon">
            <Sparkles size={16} />
          </span>
          <div>
            <strong>Flexible plans for your business</strong>
            <p>
              Plan purchasing will be available soon. Explore the options
              and compare what each plan includes.
            </p>
          </div>
        </div>
      )}

      {error && (
        <ErrorState message={error} onRetry={() => void refresh()} />
      )}

      {loading ? (
        <LoadingState label="Loading available plans" />
      ) : plans.length === 0 ? (
        <EmptyState
          title="No plans available"
          description="There are no published pricing plans at the moment. Please check back later."
        />
      ) : (
        <div className="workspace-pricing-grid">
          {plans.map((plan) => (
            <article
              className="marketing-plan-card workspace-pricing-card"
              key={plan.id}
            >
              <div className="marketing-plan-topline">
                <span className="marketing-plan-label">
                  Available plan
                </span>
                <span className="marketing-plan-check">
                  <Check size={14} />
                </span>
              </div>

              <h3>{plan.name}</h3>

              {plan.description && (
                <p className="marketing-plan-description">
                  {plan.description}
                </p>
              )}

              <div className="marketing-plan-price">
                <strong>
                  {formatMoney(plan.monthly_price, plan.currency)}
                </strong>
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
                    <strong>
                      {Number(plan.max_phone_numbers).toLocaleString()}
                    </strong>{" "}
                    {Number(plan.max_phone_numbers) === 1
                      ? "phone number"
                      : "phone numbers"}
                  </span>
                </div>
              </div>

              <ul
                className="marketing-plan-includes"
                aria-label="Included capabilities"
              >
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

              <div className="workspace-plan-footer">
                <span>
                  <Check size={13} />
                  Plan available
                </span>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
