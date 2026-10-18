import { useCallback, useEffect, useState } from "react";
import { listModes } from "./lib/api.js";
import HomeScreen from "./screens/Home.jsx";
import CaptureScreen from "./screens/Capture.jsx";
import SetupScreen from "./screens/Setup.jsx";
import StageScreen from "./screens/Stage.jsx";
import DebugDrawer from "./components/DebugDrawer.jsx";

const KEY = "\u0060"; // backtick toggles the debug drawer

export default function App() {
  const [screen, setScreen] = useState("home"); // home | capture | setup | stage
  const [sceneData, setSceneData] = useState(null); // {scene_id, objects, image_blob}
  const [characterEdits, setCharacterEdits] = useState(null); // Setup personality edits
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

  return (
    <div className="yap-app">
      {screen === "home" && <HomeScreen onGetStarted={() => setScreen("capture")} />}
      {screen === "capture" && (
        <CaptureScreen
          onImageReady={(data) => {
            setSceneData(data);
            setScreen("setup");
          }}
          onBack={() => setScreen("home")}
        />
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
          startWithEdits={(edits) => {
            setCharacterEdits(edits); // sent to /ws/session by the Stage screen
            setScreen("stage");
          }}
        />
      )}
      {screen === "stage" && sceneData && (
        <StageScreen
          sceneData={sceneData}
          modeId={modeId}
          modeName={modes.find((m) => m.id === modeId)?.name || modeId}
          difficulty={difficulty}
          selected={selected}
          characterEdits={characterEdits}
          onChangeMode={() => setScreen("setup")}
          onRetake={() => {
            setSceneData(null);
            setSelected([]);
            setScreen("capture");
          }}
          onBackHome={() => setScreen("home")}
          onDebug={pushDebug}
        />
      )}
      <DebugDrawer open={debugOpen} events={debugEvents} onClose={() => setDebugOpen(false)} />
      {screen !== "home" && (
        <footer className="app-hint">
          YAP · press <kbd>{KEY}</kbd> for the debug drawer
        </footer>
      )}
    </div>
  );
}
