import type { LineageDirection } from "../types/lineage.types";

type ExpandButtonProps = {
  direction: LineageDirection;
  loading?: boolean;
  onClick: () => void;
};

export default function ExpandButton({ direction, loading, onClick }: ExpandButtonProps) {
  return (
    <button
      type="button"
      className={`plex-expand plex-expand-${direction}`}
      title={direction === "upstream" ? "Expand upstream lineage" : "Expand downstream lineage"}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      disabled={loading}
      aria-label={direction === "upstream" ? "Expand upstream lineage" : "Expand downstream lineage"}
    >
      {loading ? "..." : "+"}
    </button>
  );
}
