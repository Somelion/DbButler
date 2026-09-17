import { useState } from "react";
import QueryIntelligence from "./QueryIntelligence";
import ExplainView from "./ExplainView";
import QueryAnalysisModal from "./QueryAnalysisModal";

export default function QueryIntelligenceScreen({ targetId }) {
  const [seedQuery, setSeedQuery] = useState(null);
  const [analysisQuery, setAnalysisQuery] = useState(null);

  return (
    <>
      <QueryIntelligence
        targetId={targetId}
        onExplain={(query) => setSeedQuery(query)}
        onAnalyzeWithAi={(query) => setAnalysisQuery(query)}
      />
      <ExplainView targetId={targetId} seedQuery={seedQuery} onSeedConsumed={() => setSeedQuery(null)} />
      {analysisQuery && (
        <QueryAnalysisModal targetId={targetId} query={analysisQuery} onClose={() => setAnalysisQuery(null)} />
      )}
    </>
  );
}
