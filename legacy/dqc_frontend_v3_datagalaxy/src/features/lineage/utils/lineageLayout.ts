import type { LineageNode, LineagePosition } from "../types/lineage.types";

export const CARD_WIDTH = 248;
export const CARD_HEIGHT = 132;
export const HORIZONTAL_SPACING = 320;
export const VERTICAL_SPACING = 118;
export const BOARD_PADDING = 360;

export type LayoutNode = LineageNode & {
  layout: LineagePosition;
};

export function expansionPosition(
  clicked: LineageNode,
  index: number,
  count: number,
  direction: "upstream" | "downstream"
): LineagePosition {
  const nextDepth = clicked.depth + (direction === "downstream" ? 1 : -1);
  const centeredIndex = index - (count - 1) / 2;
  return {
    x: nextDepth * HORIZONTAL_SPACING,
    y: (clicked as LineageNode & { y?: number }).y ?? centeredIndex * VERTICAL_SPACING,
  };
}

export function boardBounds(positions: Record<string, LineagePosition>) {
  const values = Object.values(positions);
  if (!values.length) {
    return {
      minX: 0,
      minY: 0,
      width: 900,
      height: 520,
    };
  }
  const minX = Math.min(...values.map((position) => position.x));
  const minY = Math.min(...values.map((position) => position.y));
  const maxX = Math.max(...values.map((position) => position.x + CARD_WIDTH));
  const maxY = Math.max(...values.map((position) => position.y + CARD_HEIGHT));
  return {
    minX,
    minY,
    width: Math.max(900, maxX - minX + BOARD_PADDING * 2),
    height: Math.max(520, maxY - minY + BOARD_PADDING * 2),
  };
}

export function toBoardPosition(
  position: LineagePosition,
  bounds: ReturnType<typeof boardBounds>
): LineagePosition {
  return {
    x: position.x - bounds.minX + BOARD_PADDING,
    y: position.y - bounds.minY + BOARD_PADDING,
  };
}
