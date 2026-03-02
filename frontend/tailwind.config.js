/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'dispatch-dark': '#0f172a',
        'dispatch-panel': '#1e293b',
        'dispatch-border': '#334155',
        'dispatch-accent': '#3b82f6',
        'dispatch-critical': '#ef4444',
        'dispatch-warning': '#f59e0b',
        'dispatch-success': '#22c55e',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'typing': 'typing 0.5s steps(1) forwards',
      },
    },
  },
  plugins: [],
}
