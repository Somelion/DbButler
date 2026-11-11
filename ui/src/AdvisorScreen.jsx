import Advisor from "./Advisor";

// The active tab is read from the URL inside Advisor (/t/:id/advisor/:tab).
export default function AdvisorScreen({ targetId }) {
  return <Advisor targetId={targetId} />;
}
