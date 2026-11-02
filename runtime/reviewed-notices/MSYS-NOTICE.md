# Git for Windows MSYS runtime

This notice applies to the bundled `tools/git/usr/bin/msys-2.0.dll` with SHA256
`2a89b7c31b323c42e2fb37900af087aeb27417694e630a210bc75b0369410bc7`.

It is the Git for Windows build of msys2-runtime 3.6.10-4, not the identically numbered general MSYS2 build. The DLL bytes match the official Git for Windows SDK file.

- SDK package update: https://github.com/git-for-windows/git-sdk-64/commit/465653d2adc5d0741fd3b1b0250ff4fcda951ca1
- Source commit: https://github.com/git-for-windows/msys2-runtime/tree/5a1665c8a0fb24930e55f1621441dfd98a798c15
- Build recipe and patches: https://github.com/git-for-windows/MSYS2-packages/tree/546c20097ac698c722d2c1c882d5b5fe42367246/msys2-runtime

The recipe's `msys2-runtime.commit` identifies the source commit above. Its PKGBUILD records source inputs, patches, dependencies and build/install commands. Source archive URLs and SHA256 values are recorded in manifest.json. Those archives include the pinned source and the build recipes/patches; they are available without payment at the listed URLs.

The DLL's source licensing is described in the accompanying original `CYGWIN_LICENSE.msys2-runtime`, `COPYING.LIB.msys2-runtime` and `COPYING.msys2-runtime` files. In particular, the Cygwin DLL source uses LGPLv3-or-later with the stated linking exception, with separately marked portions retaining their own terms. The package-level label “GPL” is not a substitute for these file-level terms.

No claim of ownership or relicensing is made over this component. Original copyright and license notices remain in its corresponding source. The application invokes external GNU tools using this runtime; this notice does not change the licenses of those separate tools.
