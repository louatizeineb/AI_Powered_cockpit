import type { LineageSearchResult } from "../types/lineage.types";

type LineageSearchBarProps = {
  query: string;
  searching: boolean;
  results: LineageSearchResult[];
  onQueryChange: (value: string) => void;
  onSearch: () => void;
  onSelect: (result: LineageSearchResult) => void;
};

function resultPath(result: LineageSearchResult) {
  if (result.path) return result.path;
  if (result.parent_label) return `${result.parent_label} / ${result.label}`;
  return result.node_id;
}

export default function LineageSearchBar({
  query,
  searching,
  results,
  onQueryChange,
  onSearch,
  onSelect,
}: LineageSearchBarProps) {
  return (
    <div className="plex-search">
      <div className="plex-search-row">
        <input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") onSearch();
          }}
          placeholder="Search by name, node id, or full path..."
        />
        <button type="button" onClick={onSearch} disabled={searching || !query.trim()}>
          {searching ? "Searching..." : "Search"}
        </button>
      </div>
      {results.length > 0 && (
        <div className="plex-results">
          {results.map((result) => (
            <button key={result.id} type="button" onClick={() => onSelect(result)}>
              <strong>{result.label}</strong>
              <span>{result.type}</span>
              <small>{resultPath(result)}</small>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
