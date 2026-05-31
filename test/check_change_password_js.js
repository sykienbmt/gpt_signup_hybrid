const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const file = path.join(root, 'web', 'static', 'change-password.js');
const source = fs.readFileSync(file, 'utf8');

new vm.Script(source, { filename: file });
console.log('OK change password JS syntax');
