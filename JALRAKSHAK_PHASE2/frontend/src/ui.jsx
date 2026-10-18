import { useState } from "react";
import { useT } from "./i18n.js";

// A button that asks for a second click before doing something that changes data.
export function ConfirmButton({ label, confirmLabel, question, onConfirm, disabled, className = "btn" }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!asking) {
    return (
      <button type="button" className={className} disabled={disabled} onClick={() => setAsking(true)}>
        {label}
      </button>
    );
  }
  return (
    <span className="confirm" role="group" aria-label={question}>
      <span className="confirm-q">{question}</span>
      <span className="confirm-actions">
        <button
          type="button"
          className="btn btn-danger"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await onConfirm();
            } finally {
              setBusy(false);
              setAsking(false);
            }
          }}
        >
          {busy ? "Working…" : confirmLabel}
        </button>
        <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => setAsking(false)}>
          Cancel
        </button>
      </span>
    </span>
  );
}

export function Priority({ value }) {
  return (
    <span className="pips" role="img" aria-label={`Priority ${value} of 5`} title={`Priority ${value} of 5`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} className={i <= value ? "pip on" : "pip"} />
      ))}
    </span>
  );
}

const STATUS_LABEL = {
  pending: "Pending", approved: "Approved", partial: "Partly served", rejected: "Rejected", cancelled: "Cancelled",
};
export function StatusBadge({ status }) {
  const t = useT();
  return <span className={`badge badge-${status}`}>{t(STATUS_LABEL[status] || status)}</span>;
}

export function Empty({ title, children }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <p className="empty-body">{children}</p>}
    </div>
  );
}
