/**
 * Configuration Tailwind du portail RAMPE (charte Cap Terminal S.A.).
 *
 * Le CSS est compilé en local, sans Node, avec l'exécutable autonome Tailwind v3 :
 *   tools/tailwind/tailwindcss.exe -c tailwind.config.js -i static/css/tailwind.input.css -o static/css/tailwind.css --minify
 * À relancer après toute modification des classes dans templates/ ou static/js/.
 */
module.exports = {
  content: ['./templates/**/*.html', './static/js/**/*.js'],
  theme: {
    extend: {
      colors: {
        ctsa: {
          slate: '#2c3741',
          slatedark: '#232c34',
          secondary: '#2c3e50',
          subnav: '#687077',
          blue: '#3498db',
          bluehover: '#217dbb',
          green: '#008751',
          red: '#e74c3c',
          bgalt: '#f4f4f4',
        },
      },
      fontFamily: {
        sans: ['"Open Sans"', '"Helvetica Neue"', 'Arial', 'sans-serif'],
        heading: ['"Raleway"', '"Open Sans"', 'Arial', 'sans-serif'],
      },
    },
  },
};
