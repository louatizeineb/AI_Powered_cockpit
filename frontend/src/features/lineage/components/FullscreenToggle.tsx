type FullscreenToggleProps = {
  active: boolean;
  onToggle: () => void;
};

export default function FullscreenToggle({ active, onToggle }: FullscreenToggleProps) {
  return (
    <button
      type="button"
      className="plex-icon-button"
      onClick={onToggle}
      title={active ? "Exit fullscreen" : "Enter fullscreen"}
      aria-label={active ? "Exit fullscreen" : "Enter fullscreen"}
    >
      {active ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M9 4v5H4" />
          <path d="M15 4v5h5" />
          <path d="M9 20v-5H4" />
          <path d="M15 20v-5h5" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M8 4H4v4" />
          <path d="M16 4h4v4" />
          <path d="M8 20H4v-4" />
          <path d="M16 20h4v-4" />
        </svg>
      )}
    </button>
  );
}
