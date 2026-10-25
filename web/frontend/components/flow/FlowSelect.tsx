import React, { useEffect, useRef, useState } from "react";

// ─── Flow-themed custom select ────────────────────────────────

export interface FlowSelectProps {
  value: string;
  options: string[];
  disabled: boolean;
  getLabel: (v: string) => string;
  onChange: (v: string) => void;
}

export function FlowSelect({ value, options, disabled, getLabel, onChange }: FlowSelectProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="flow-custom-select" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        className={`flow-custom-select-trigger${open ? " is-open" : ""}${disabled ? " is-disabled" : ""}`}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="flow-custom-select-label">{getLabel(value)}</span>
        <svg
          width="11" height="11" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
          className={`flow-custom-select-chevron${open ? " is-open" : ""}`}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div className="flow-custom-select-menu">
          {options.map((opt) => {
            const isActive = opt === value;
            return (
              <button
                key={opt}
                type="button"
                className={`flow-custom-select-opt${isActive ? " is-active" : ""}`}
                onClick={() => { onChange(opt); setOpen(false); }}
              >
                {isActive ? (
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="flow-custom-select-check">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                ) : (
                  <span className="flow-custom-select-check" />
                )}
                {getLabel(opt)}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
