import type { LineageDirection } from "../types/lineage.types";

type ExpandButtonProps = {
  direction: LineageDirection;
  loading?: boolean;
  expanded?: boolean;
  onClick: () => void;
};

export default function ExpandButton({ direction, loading, expanded, onClick }: ExpandButtonProps) {
  const action = expanded ? "Collapse" : "Expand";
  const label = `${action} ${direction} lineage`;
  return (
    <button
      type="button"
      className={`plex-expand plex-expand-${direction}`}
      title={label}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      disabled={loading}
      aria-label={label}
    >
      {loading ? "..." : expanded ? "-" : "+"}
    </button>
  );
}
