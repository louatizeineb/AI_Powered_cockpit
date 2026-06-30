import { useState } from "react";
import type { HighlightDirection } from "../types/lineage.types";
import LineageHighlightPalette from "./LineageHighlightPalette";

type LineageNodeMenuProps = {
  hasHighlight: boolean;
  onApplyHighlight: (direction: HighlightDirection, color: string) => void;
  onClearNodeHighlight: () => void;
  onClearAllHighlights: () => void;
};

export default function LineageNodeMenu({
  hasHighlight,
  onApplyHighlight,
  onClearNodeHighlight,
  onClearAllHighlights,
}: LineageNodeMenuProps) {
  const [open, setOpen] = useState(false);
  const [pendingDirection, setPendingDirection] = useState<HighlightDirection | null>(null);

  function close() {
    setOpen(false);
    setPendingDirection(null);
  }

  return (
    <div className="plex-node-menu" onClick={(event) => event.stopPropagation()}>
      <button
        type="button"
        className="plex-node-menu-trigger"
        title="Node actions"
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
          setPendingDirection(null);
        }}
      >
        ...
      </button>
      {open && (
        <div className="plex-node-menu-popover">
          {!pendingDirection && (
            <>
              <button type="button" onClick={() => setPendingDirection("downstream")}>
                Highlight downstream lineage
              </button>
              <button type="button" onClick={() => setPendingDirection("upstream")}>
                Highlight upstream lineage
              </button>
              <button type="button" onClick={() => setPendingDirection("branch")}>
                Highlight visible branch
              </button>
              {hasHighlight && (
                <button type="button" onClick={() => setPendingDirection("branch")}>
                  Change highlight color
                </button>
              )}
              {hasHighlight && <button type="button" onClick={onClearNodeHighlight}>Clear highlight</button>}
              <button type="button" onClick={onClearAllHighlights}>Clear all highlights</button>
            </>
          )}
          {pendingDirection && (
            <LineageHighlightPalette
              onPick={(color) => {
                onApplyHighlight(pendingDirection, color);
                close();
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}
