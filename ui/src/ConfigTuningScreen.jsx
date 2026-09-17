import { useState } from "react";
import HardwareProfile from "./HardwareProfile";
import ConfigTuning from "./ConfigTuning";

export default function ConfigTuningScreen({ targetId }) {
  const [hardwareProfileVersion, setHardwareProfileVersion] = useState(0);

  return (
    <>
      <HardwareProfile targetId={targetId} onSaved={() => setHardwareProfileVersion((v) => v + 1)} />
      <ConfigTuning key={hardwareProfileVersion} targetId={targetId} />
    </>
  );
}
