// Provider/model picker for the chat panel.
// Reads /api/settings/providers (already used by Settings tab) and
// persists the selection to localStorage so the user keeps the same
// chat model across reloads.

import { useEffect, useMemo, useState } from "react";

const STORAGE_KEY = "vulnflow.chat.model";

export function ModelSelector({ providers, value, onChange }) {
  const flatOptions = useMemo(() => flattenOptions(providers || []), [providers]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (value?.provider && value?.model) return;
    if (!flatOptions.length) return;
    const stored = readStored();
    const match = stored
      ? flatOptions.find((o) => o.provider === stored.provider && o.model === stored.model)
      : null;
    onChange(match || flatOptions[0]);
  }, [flatOptions, value, onChange]);

  const label = value?.model
    ? `${value.provider} · ${value.model}`
    : "Select model";

  return (
    <div className="chat-model">
      <button
        type="button"
        className="chat-model__button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={!flatOptions.length}
      >
        <span className="chat-model__label">{label}</span>
        <span className="chat-model__chev" aria-hidden>▾</span>
      </button>
      {open ? (
        <ul className="chat-model__menu" role="listbox">
          {flatOptions.map((opt) => (
            <li key={`${opt.provider}|${opt.model}`}>
              <button
                type="button"
                className={`chat-model__item ${
                  value?.provider === opt.provider && value?.model === opt.model
                    ? "chat-model__item--active"
                    : ""
                }`}
                onClick={() => {
                  onChange(opt);
                  writeStored(opt);
                  setOpen(false);
                }}
              >
                <span className="chat-model__provider">{opt.provider}</span>
                <span className="chat-model__model">{opt.model}</span>
              </button>
            </li>
          ))}
          {!flatOptions.length ? (
            <li className="chat-model__empty">No providers configured in conf.yaml</li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

function flattenOptions(providers) {
  const out = [];
  for (const provider of providers || []) {
    const id = provider.id || provider.provider || provider.name;
    const models = provider.models || [];
    for (const model of models) {
      const modelId = typeof model === "string" ? model : model?.id || model?.name;
      if (!id || !modelId) continue;
      out.push({ provider: id, model: modelId, label: provider.label || id });
    }
  }
  return out;
}

function readStored() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed?.provider && parsed?.model) return parsed;
  } catch {}
  return null;
}

function writeStored(value) {
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ provider: value.provider, model: value.model }),
    );
  } catch {}
}
