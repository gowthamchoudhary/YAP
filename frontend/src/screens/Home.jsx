import YapLogo from "../components/YapLogo.jsx";

const FEATURES = [
  {
    title: "Capture",
    text: "Take a photo or point your camera at a scene.",
    icon: "📷",
    color: "#c8b7f5", // lavender
  },
  {
    title: "Create",
    text: "Objects become unique AI characters with personalities.",
    icon: "✦",
    color: "#f8e08e", // butter yellow
  },
  {
    title: "Converse",
    text: "They talk to each other naturally — not just you.",
    icon: "💬",
    color: "#9df3c7", // mint
  },
  {
    title: "Build",
    text: "Relationships, memories and an evolving world over time.",
    icon: "❤",
    color: "#f9b3c2", // pink
  },
];

const BUBBLES = [
  "Yo! Ready for another adventure?",
  "I've been here longer than you remember…",
  "Don't forget to hydrate, hero!",
];

export default function HomeScreen({ onGetStarted }) {
  return (
    <div className="home">
      {/* sticky pill nav */}
      <header className="home-nav">
        <a className="home-logo" href="#" onClick={(e) => e.preventDefault()}>
          YAP
        </a>
        <nav className="home-links">
          <a href="#features" className="active">Features</a>
          <a href="#how">How it works</a>
          <a href="#gallery">Gallery</a>
        </nav>
        <button className="btn-pill dark" onClick={onGetStarted}>
          Get Started <span className="arrow">→</span>
        </button>
      </header>

      {/* hero */}
      <section className="hero">
        <div className="hero-copy">
          <p className="hero-kicker">
            <span className="scribble-script">Ordinary Objects.</span>
            <span className="scribble-script">Extraordinary Conversations.</span>
          </p>
          <h1 className="hero-title" aria-label="YAP">
            <span className="hero-yap">
              Y<span className="hero-dot">·</span>A<span className="hero-dot">·</span>P
            </span>
          </h1>
          <h2 className="hero-sub">
            Turn the world around you into living, talking characters.
          </h2>
          <p className="hero-body">
            Scan your surroundings, give everyday objects a voice, and watch them come
            to life in natural conversations with each other — and you.
          </p>
          <button className="btn-pill dark hero-cta" onClick={onGetStarted}>
            Try YAP Now <span className="arrow">→</span>
          </button>
          <p className="hero-foot scribble-script">No complex setup. Just your camera.</p>
        </div>

        <div className="hero-photo-wrap">
          <span className="tape tape-yellow" aria-hidden />
          <img src="/hero-photo.jpg" alt="A desk with a toy, a water bottle and a lamp" className="hero-photo" />
          {BUBBLES.map((t, i) => (
            <span key={i} className={`hero-bubble bubble-${i}`}>{t}</span>
          ))}
        </div>
      </section>

      {/* 4 feature cards */}
      <section id="features" className="feature-row">
        {FEATURES.map((f) => (
          <article key={f.title} className="feature-card">
            <span className="feature-icon" style={{ background: f.color }}>{f.icon}</span>
            <h3>{f.title}</h3>
            <p>{f.text}</p>
          </article>
        ))}
      </section>

      {/* storytelling section */}
      <section id="how" className="story">
        <div className="story-visual">
          <span className="tape tape-yellow" aria-hidden />
          <div className="story-frame">
            <img src="/hero-photo.jpg" alt="Objects talking on a desk" />
            <span className="story-bubble bubble-a">I'm feeling creative today!</span>
            <span className="story-bubble bubble-b">Let's plan something epic!</span>
            <span className="story-bubble bubble-c">Hydration first, hero.</span>
            <div className="story-chips">
              <span className="chip-plant">Planty</span>
              <span className="chip-toy">Spidey</span>
              <span className="chip-bottle">Bottle</span>
            </div>
            <div className="story-toolbar">
              <span className="tb-icon">🖼</span>
              <span className="tb-shutter" />
              <span className="tb-icon">↻</span>
            </div>
          </div>
        </div>
        <div className="story-copy">
          <p className="scribble-script">A more alive world</p>
          <h3>
            Real objects.<br />Real personalities.
          </h3>
          <p>
            Each object is an independent AI character with its own voice, memories, and
            relationships. They react to each other, develop inside jokes, and decide
            when to join the conversation.
          </p>
          <button className="btn-pill dark" onClick={onGetStarted}>
            <span className="play-dot">▶</span> See it in action <span className="arrow">→</span>
          </button>
        </div>
      </section>

      <footer className="home-footer">
        <span className="scribble-script">
          Same objects. <br /> New stories every day.
        </span>
        <span className="heart-scribble" aria-hidden>♡</span>
      </footer>
    </div>
  );
}
