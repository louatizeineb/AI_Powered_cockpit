type FullscreenToggleProps = {
  active: boolean;
  onToggle: () => void;
};

export default function FullscreenToggle({ active, onToggle }: FullscreenToggleProps) {
  return (
    <button type="button" onClick={onToggle} title={active ? "Exit fullscreen" : "Enter fullscreen"}>
      {active ? "Exit full" : "Full"}
    </button>
  );
}

