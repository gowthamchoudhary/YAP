import { useCallback, useEffect, useRef, useState } from "react";
import { listModes } from "./lib/api.js";
import CaptureScreen from "./screens/Capture.jsx";
import SetupScreen from "./screens/Setup.jsx";
import StageScreen from "./screens/Stage.jsx";
import DebugDrawer from "./components/DebugDrawer.jsx";

const KEY = "\u0060"; // backtick toggles the debug drawer

export default function App() {
  const [screen, setScreen] = useState("capture"); // capture | setup | stage
  const [sceneData, setSceneData] = useState(null); // {scene_id, objects}
  const [modes, setModes] = useState([]);
  const [modeId, setModeId] = useState("conspiracy");
  const [difficulty, setDifficulty] = useState("normal");
  const [selected, setSelected] = useState([]);
  const [debugEvents, setDebugEvents] = useState([]);
  const [debugOpen, setDebugOpen] = useState(false);

  useEffect(() => {
    listModes()
      .then((m) => {
        setModes(m);
        if (m.length && !m.some((x) => x.id === modeId)) setModeId(m[0].id);
      })
      .catch((e) => console.error("modes", e));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === KEY) setDebugOpen((v) => !v);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const pushDebug = useCallback((ev) => {
    setDebugEvents((prev) => [...prev.slice(-200), { at: new Date().toLocaleTimeString(), ...ev }]);
  }, []);

  const onSceneReady = useCallback((data) => {
    setSceneData(data);
    setScreen("setup");
  }, []);

  const onSessionEnded = useCallback((summary) => {
    pushDebug({ kind: "ended", summary });
  }, [pushDebug]);

  return (
    <div className={`app accent-${modeId}`}>
      {screen === "capture" && (
        <CaptureScreen onImageReady={onSceneReady} />
      )}
      {screen === "setup" && sceneData && (
        <SetupScreen
          sceneData={sceneData}
          modes={modes}
          modeId={modeId}
          setModeId={setModeId}
          difficulty={difficulty}
          setDifficulty={setDifficulty}
          selected={selected}
          setSelected={setSelected}
          onRetake={() => setScreen("capture")}
          onStart={() => setScreen("stage")}
        />
      )}
      {screen === "stage" && sceneData && (
        <StageScreen
          sceneData={sceneData}
          modeId={modeId}
          modeName={modes.find((m) => m.id === modeId)?.name || modeId}
          difficulty={difficulty}
          selected={selected}
          onChangeMode={() => {
            // Same photo, different mode: back to Setup keeping the scene + selection
            setScreen("setup");
          }}
          onRetake={() => {
            setSceneData(null);
            setSelected([]);
            setScreen("capture");
          }}
          onDebug={pushDebug}
          onEnded={onSessionEnded}
        />
      )}
      <DebugDrawer open={debugOpen} events={debugEvents} onClose={() => setDebugOpen(false)} />
      <footer className="app-hint">
        YAP · press <kbd>{KEY}</kbd> for the debug drawer
      </footer>
    </div>
  );
}
