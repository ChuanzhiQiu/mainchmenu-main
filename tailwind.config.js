/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./Menu/templates/**/*.html', './Menu/templatetags/**/*.py'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#fffbeb',
          100: '#fef3c7',
          500: '#f59e0b',
          600: '#d97706',
          700: '#b45309',
        },
      },
    },
  },
  // Clases construidas dinámicamente (JS/plantillas) que el escáner no puede ver.
  safelist: [
    // Paleta de marca (usada desde plantillas y estilos legacy).
    {
      pattern: /^(bg|text|border|ring|from|to)-brand-(50|100|500|600|700)$/,
      variants: ['hover', 'focus'],
    },
    'bg-slate-950/75',
    'bg-black/60',
    'transition-colors',
    // Estados de toasts y badges generados desde JS.
    'bg-amber-50',
    'border-amber-200',
    'text-amber-900',
    'bg-rose-50',
    'border-rose-200',
    'text-rose-900',
    'bg-emerald-50',
    'border-emerald-200',
    'text-emerald-900',
    'bg-blue-50',
    'border-blue-200',
    'text-blue-900',
    'translate-y-2',
    'opacity-0',
  ],
  plugins: [],
};
