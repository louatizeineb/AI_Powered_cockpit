const items = [
  ["technical", "Tables / fields"],
  ["usage", "Usages / datasets"],
  ["processing", "Processing"],
  ["control", "Controls"],
];

export default function LineageLegend() {
  return (
    <div className="plex-legend" aria-label="Lineage legend">
      {items.map(([tone, label]) => (
        <span key={tone}>
          <i className={`plex-legend-dot ${tone}`} />
          {label}
        </span>
      ))}
    </div>
  );
}
