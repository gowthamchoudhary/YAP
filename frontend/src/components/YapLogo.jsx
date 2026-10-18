export default function YapLogo({ className = "", style = {} }) {
  // Hand-drawn blob "YAP" wordmark matching the sticker logo (SVG version)
  return (
    <svg
      className={`yap-logo ${className}`}
      style={style}
      viewBox="0 0 240 100"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="YAP"
      role="img"
    >
      {/* spark marks */}
      <g stroke="#171717" strokeWidth="4" strokeLinecap="round" fill="none">
        <path d="M12 30 Q 18 38 12 46" />
        <path d="M4 52 Q 10 60 4 68" />
        <path d="M228 34 Q 222 42 228 50" />
        <path d="M236 58 Q 230 66 236 74" />
      </g>
      {/* black blob outline merged as one background group */}
      <g fill="#171717">
        {/* Y */}
        <path d="M40 12 C 56 8 68 14 66 34 C 64 52 62 60 58 68 C 56 74 60 80 70 80 C 80 80 86 74 84 64 L 84 40 C 84 26 92 16 108 14 C 122 12 132 20 130 34 L 126 66 C 124 78 130 86 142 84 C 154 82 158 74 158 64 L 154 30 C 152 16 162 10 176 12 C 190 14 194 24 192 36 L 188 70 C 186 82 178 92 162 94 C 144 96 132 92 124 88 C 112 96 96 100 80 98 C 60 96 46 88 44 72 C 42 58 46 48 50 40 C 46 36 42 30 42 24 Z" />
      </g>
      {/* cream inner letters (negative-space roughly Y / A / P) */}
      <g fill="#fdf2dc" stroke="#fdf2dc" strokeWidth="6" strokeLinejoin="round" strokeLinecap="round">
        {/* Y stem */}
        <path d="M58 26 C 62 40 62 52 58 66" />
        <path d="M112 30 C 108 46 106 58 108 72" />
        <path d="M120 30 C 126 46 130 58 134 72" />
        <path d="M170 30 C 168 46 168 60 172 74" />
        <path d="M196 34 C 196 46 194 60 192 72" />
      </g>
      {/* A's dot (the '.' in 'Y.A.P') */}
      <circle cx="144" cy="86" r="5" fill="#171717" />
    </svg>
  );
}
