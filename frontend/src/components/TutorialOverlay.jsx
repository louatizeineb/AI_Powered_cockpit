import React, { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, HelpCircle, X } from "lucide-react";
import {
  getTutorialScreen,
  isTutorialDismissed,
  setTutorialDismissed,
  tutorialIndex,
} from "../services/tutorialService";

export default function TutorialOverlay({
  namespace,
  screenKey,
  tutorials,
  onScreenChange,
  autoOpen = true,
}) {
  const [open, setOpen] = useState(false);
  const [activeKey, setActiveKey] = useState(screenKey);
  const [dontShow, setDontShow] = useState(false);

  useEffect(() => {
    setActiveKey(screenKey);
  }, [screenKey]);

  useEffect(() => {
    if (autoOpen && !isTutorialDismissed(namespace)) setOpen(true);
  }, [autoOpen, namespace]);

  const active = useMemo(() => getTutorialScreen(tutorials, activeKey), [activeKey, tutorials]);
  const index = tutorialIndex(tutorials, active?.key);
  const canGoBack = index > 0;
  const canGoNext = index < tutorials.length - 1;

  function close() {
    if (dontShow) setTutorialDismissed(namespace, true);
    setOpen(false);
  }

  function move(offset) {
    const next = tutorials[index + offset];
    if (!next) return;
    setActiveKey(next.key);
    onScreenChange?.(next.key);
  }

  return (
    <>
      <button type="button" className="tutorial-trigger" onClick={() => setOpen(true)}>
        <HelpCircle size={15} />
        Screen guide
      </button>
      {open && active && <div className="tutorial-backdrop" role="presentation">
        <section className="tutorial-panel" role="dialog" aria-modal="true" aria-labelledby="tutorial-title">
          <header className="tutorial-header">
            <div>
              <span className="next-overline">Quick tutorial</span>
              <h3 id="tutorial-title">{active.title}</h3>
              <p>{active.summary}</p>
            </div>
            <button type="button" aria-label="Close tutorial" onClick={close}><X size={18} /></button>
          </header>

          <div className="tutorial-content">
            <div className="tutorial-section">
              <h4>What this screen is for</h4>
              <p>{active.purpose}</p>
            </div>
            <div className="tutorial-section">
              <h4>Buttons and controls</h4>
              <div className="tutorial-control-list">
                {active.controls.map((control) => (
                  <article key={control.label}>
                    <strong>{control.label}</strong>
                    <p>{control.description}</p>
                  </article>
                ))}
              </div>
            </div>
            {!!active.tips?.length && <div className="tutorial-section">
              <h4>How to use it safely</h4>
              <ul>
                {active.tips.map((tip) => <li key={tip}>{tip}</li>)}
              </ul>
            </div>}
          </div>

          <footer className="tutorial-footer">
            <label>
              <input type="checkbox" checked={dontShow} onChange={(event) => setDontShow(event.target.checked)} />
              Do not show automatically again
            </label>
            <div className="tutorial-actions">
              <button type="button" onClick={() => move(-1)} disabled={!canGoBack}><ChevronLeft size={15} />Previous</button>
              <span>{index + 1} / {tutorials.length}</span>
              <button type="button" onClick={() => move(1)} disabled={!canGoNext}>Next<ChevronRight size={15} /></button>
              <button type="button" className="primary" onClick={close}>Got it</button>
            </div>
          </footer>
        </section>
      </div>}
    </>
  );
}
