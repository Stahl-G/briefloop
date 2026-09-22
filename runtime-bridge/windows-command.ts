// CreateProcessW permits 32,767 UTF-16 code units INCLUDING the final NUL.
// Count the argument quoting Node/libuv applies when windowsVerbatimArguments
// is false: quotes and preceding/trailing backslashes can expand the argv.
// See https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessw
function quotedLength(argument:string):number {
 if (argument.length && !/[ \t"]/.test(argument)) return argument.length;
 let extra = 2, slashes = 0;
 for (const character of argument) {
  if (character === '\\') slashes++;
  else {
   if (character === '"') extra += slashes + 1;
   slashes = 0;
  }
 }
 return argument.length + extra + slashes;
}

export function checkWindowsCommandLine(bin:string, args:string[]) {
 // Each argument contributes one separator, with the last being the NUL.
 const length = [bin, ...args].reduce((size, argument) => size + quotedLength(argument) + 1, 0);
 if (length > 32767) throw Object.assign(new Error('Windows 命令行超出 32767 UTF-16 字符限制（包括程序路径、参数转义及结束符）'), {code:'E2BIG'});
}
