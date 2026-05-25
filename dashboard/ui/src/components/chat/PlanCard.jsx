// Plan card rendered when the agent emits a plan_visualization envelope part.
// Mirrors the PlanCard pattern from misc/OpenGenerativeUI generative-ui.

export function PlanCard({ approach, technology, keyElements }) {
  return (
    <div className="chat-plan-card">
      <div className="chat-plan-card__header">
        <span className="chat-plan-card__icon" aria-hidden>◇</span>
        <span className="chat-plan-card__label">Visualization plan</span>
      </div>
      <div className="chat-plan-card__row">
        <span className="chat-plan-card__field">Approach</span>
        <span className="chat-plan-card__value">{approach}</span>
      </div>
      <div className="chat-plan-card__row">
        <span className="chat-plan-card__field">Tech</span>
        <span className="chat-plan-card__value">{technology}</span>
      </div>
      {keyElements && keyElements.length ? (
        <ul className="chat-plan-card__list">
          {keyElements.map((item, idx) => (
            <li key={idx}>{item}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
