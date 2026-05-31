const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const file = path.join(root, 'web', 'static', 'check-payment.js');
const source = fs.readFileSync(file, 'utf8');

new vm.Script(source, { filename: file });
console.log('OK check payment JS syntax');
