const HIGHLIGHT_COLORS = ["#2F66FF", "#12A36A", "#7C3AED", "#F59E0B", "#EC4899", "#EF4444", "#0F766E"];

type LineageHighlightPaletteProps = {
  onPick: (color: string) => void;
};

export default function LineageHighlightPalette({ onPick }: LineageHighlightPaletteProps) {
  return (
    <div className="plex-highlight-palette">
      {HIGHLIGHT_COLORS.map((color) => (
        <button
          key={color}
          type="button"
          title={color}
          className="plex-highlight-swatch"
          style={{ background: color }}
          onClick={(event) => {
            event.stopPropagation();
            onPick(color);
          }}
        />
      ))}
    </div>
  );
}

