/**
 * Tailwind Configuration
 * Custom theme configuration for Tokio Marine platform
 */

tailwind.config = {
    theme: {
      extend: {
        colors: {
          primary: '#00e5ff',
          secondary: '#00ff9d',
          accent: '#7928ca',
          danger: '#ff4d6a',
          dark: '#050a14',
          'dark-blue': '#0d1630',
          'dark-card': '#101b38',
          'dark-panel': '#0d1630cc',
          'muted': '#6e87b9',
          'text-primary': '#e8eeff',
        },
        fontFamily: {
          sans: ['Inter', 'system-ui', 'sans-serif'],
          mono: ['JetBrains Mono', 'monospace'],
        },
        boxShadow: {
          'neon': '0 0 5px #00e5ff, 0 0 10px #00e5ff, 0 0 15px #00e5ff',
          'neon-lg': '0 0 10px #00e5ff, 0 0 20px #00e5ff, 0 0 30px #00e5ff',
          'card': '0 4px 20px rgba(0, 0, 0, 0.4)',
          'card-hover': '0 8px 30px rgba(0, 229, 255, 0.2)',
        },
        animation: {
          'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
          'float': 'float 3s ease-in-out infinite',
        },
        keyframes: {
          float: {
            '0%, 100%': { transform: 'translateY(0)' },
            '50%': { transform: 'translateY(-10px)' },
          }
        },
        backgroundImage: {
          'gradient-radial': 'radial-gradient(var(--tw-gradient-stops))',
        }
      }
    }
  };