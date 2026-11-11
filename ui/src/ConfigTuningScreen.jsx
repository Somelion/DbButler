import HardwareProfile from "./HardwareProfile";
import ConfigTuning from "./ConfigTuning";

// The recommendations are sized from the saved Hardware Profile, so saving a
// profile invalidates ConfigTuning's query (see HardwareProfile's save) and
// the table refetches in place — no remount needed.
export default function ConfigTuningScreen({ targetId }) {
  return (
    <>
      <HardwareProfile targetId={targetId} />
      <ConfigTuning targetId={targetId} />
    </>
  );
}
