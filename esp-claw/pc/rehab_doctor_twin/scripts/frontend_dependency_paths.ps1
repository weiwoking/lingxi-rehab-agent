function Initialize-FrontendDependencyPaths {
    param([Parameter(Mandatory = $true)][string]$FrontendDir)
    $frontendRoot = [IO.Path]::GetFullPath($FrontendDir).TrimEnd('\')
    $storeRoot = Join-Path $frontendRoot 'node_modules\.pnpm'
    $activeLinks = @(
        ,@('node_modules\.pnpm\@vitejs+plugin-react@6.0.3_vite@8.1.5\node_modules\@rolldown\pluginutils', 'node_modules\.pnpm\@rolldown+pluginutils@1.0.1\node_modules\@rolldown\pluginutils')
        ,@('node_modules\.pnpm\@vitejs+plugin-react@6.0.3_vite@8.1.5\node_modules\vite', 'node_modules\.pnpm\vite@8.1.5\node_modules\vite')
        ,@('node_modules\.pnpm\fdir@6.5.0_picomatch@4.0.5\node_modules\picomatch', 'node_modules\.pnpm\picomatch@4.0.5\node_modules\picomatch')
        ,@('node_modules\.pnpm\lightningcss@1.33.0\node_modules\detect-libc', 'node_modules\.pnpm\detect-libc@2.1.2\node_modules\detect-libc')
        ,@('node_modules\.pnpm\lightningcss@1.33.0\node_modules\lightningcss-win32-x64-msvc', 'node_modules\.pnpm\lightningcss-win32-x64-msvc@1.33.0\node_modules\lightningcss-win32-x64-msvc')
        ,@('node_modules\.pnpm\lucide-react@1.25.0_react@19.2.7\node_modules\react', 'node_modules\.pnpm\react@19.2.7\node_modules\react')
        ,@('node_modules\.pnpm\postcss@8.5.20\node_modules\nanoid', 'node_modules\.pnpm\nanoid@3.3.16\node_modules\nanoid')
        ,@('node_modules\.pnpm\postcss@8.5.20\node_modules\picocolors', 'node_modules\.pnpm\picocolors@1.1.1\node_modules\picocolors')
        ,@('node_modules\.pnpm\postcss@8.5.20\node_modules\source-map-js', 'node_modules\.pnpm\source-map-js@1.2.1\node_modules\source-map-js')
        ,@('node_modules\.pnpm\react-dom@19.2.7_react@19.2.7\node_modules\react', 'node_modules\.pnpm\react@19.2.7\node_modules\react')
        ,@('node_modules\.pnpm\react-dom@19.2.7_react@19.2.7\node_modules\scheduler', 'node_modules\.pnpm\scheduler@0.27.0\node_modules\scheduler')
        ,@('node_modules\.pnpm\react-router-dom@7.18.1_rea_70a7e8da4ffd065e40018c221fb97fbe\node_modules\react', 'node_modules\.pnpm\react@19.2.7\node_modules\react')
        ,@('node_modules\.pnpm\react-router-dom@7.18.1_rea_70a7e8da4ffd065e40018c221fb97fbe\node_modules\react-dom', 'node_modules\.pnpm\react-dom@19.2.7_react@19.2.7\node_modules\react-dom')
        ,@('node_modules\.pnpm\react-router-dom@7.18.1_rea_70a7e8da4ffd065e40018c221fb97fbe\node_modules\react-router', 'node_modules\.pnpm\react-router@7.18.1_react-d_2879923684311b3c119c4a81033d7b21\node_modules\react-router')
        ,@('node_modules\.pnpm\react-router@7.18.1_react-d_2879923684311b3c119c4a81033d7b21\node_modules\cookie', 'node_modules\.pnpm\cookie@1.1.1\node_modules\cookie')
        ,@('node_modules\.pnpm\react-router@7.18.1_react-d_2879923684311b3c119c4a81033d7b21\node_modules\react', 'node_modules\.pnpm\react@19.2.7\node_modules\react')
        ,@('node_modules\.pnpm\react-router@7.18.1_react-d_2879923684311b3c119c4a81033d7b21\node_modules\react-dom', 'node_modules\.pnpm\react-dom@19.2.7_react@19.2.7\node_modules\react-dom')
        ,@('node_modules\.pnpm\react-router@7.18.1_react-d_2879923684311b3c119c4a81033d7b21\node_modules\set-cookie-parser', 'node_modules\.pnpm\set-cookie-parser@2.7.2\node_modules\set-cookie-parser')
        ,@('node_modules\.pnpm\rolldown@1.1.5\node_modules\@oxc-project\types', 'node_modules\.pnpm\@oxc-project+types@0.139.0\node_modules\@oxc-project\types')
        ,@('node_modules\.pnpm\rolldown@1.1.5\node_modules\@rolldown\binding-win32-x64-msvc', 'node_modules\.pnpm\@rolldown+binding-win32-x64-msvc@1.1.5\node_modules\@rolldown\binding-win32-x64-msvc')
        ,@('node_modules\.pnpm\rolldown@1.1.5\node_modules\@rolldown\pluginutils', 'node_modules\.pnpm\@rolldown+pluginutils@1.0.1\node_modules\@rolldown\pluginutils')
        ,@('node_modules\.pnpm\tinyglobby@0.2.17\node_modules\fdir', 'node_modules\.pnpm\fdir@6.5.0_picomatch@4.0.5\node_modules\fdir')
        ,@('node_modules\.pnpm\tinyglobby@0.2.17\node_modules\picomatch', 'node_modules\.pnpm\picomatch@4.0.5\node_modules\picomatch')
        ,@('node_modules\.pnpm\vite@8.1.5\node_modules\lightningcss', 'node_modules\.pnpm\lightningcss@1.33.0\node_modules\lightningcss')
        ,@('node_modules\.pnpm\vite@8.1.5\node_modules\picomatch', 'node_modules\.pnpm\picomatch@4.0.5\node_modules\picomatch')
        ,@('node_modules\.pnpm\vite@8.1.5\node_modules\postcss', 'node_modules\.pnpm\postcss@8.5.20\node_modules\postcss')
        ,@('node_modules\.pnpm\vite@8.1.5\node_modules\rolldown', 'node_modules\.pnpm\rolldown@1.1.5\node_modules\rolldown')
        ,@('node_modules\.pnpm\vite@8.1.5\node_modules\tinyglobby', 'node_modules\.pnpm\tinyglobby@0.2.17\node_modules\tinyglobby')
        ,@('node_modules\@vitejs\plugin-react', 'node_modules\.pnpm\@vitejs+plugin-react@6.0.3_vite@8.1.5\node_modules\@vitejs\plugin-react')
        ,@('node_modules\lucide-react', 'node_modules\.pnpm\lucide-react@1.25.0_react@19.2.7\node_modules\lucide-react')
        ,@('node_modules\react', 'node_modules\.pnpm\react@19.2.7\node_modules\react')
        ,@('node_modules\react-dom', 'node_modules\.pnpm\react-dom@19.2.7_react@19.2.7\node_modules\react-dom')
        ,@('node_modules\react-router-dom', 'node_modules\.pnpm\react-router-dom@7.18.1_rea_70a7e8da4ffd065e40018c221fb97fbe\node_modules\react-router-dom')
        ,@('node_modules\three', 'node_modules\.pnpm\three@0.185.1\node_modules\three')
        ,@('node_modules\vite', 'node_modules\.pnpm\vite@8.1.5\node_modules\vite')
    )
    foreach ($pair in $activeLinks) {
        $link = [IO.Path]::GetFullPath((Join-Path $frontendRoot $pair[0]))
        $target = [IO.Path]::GetFullPath((Join-Path $frontendRoot $pair[1]))
        if (-not $link.StartsWith($frontendRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
            -not $target.StartsWith($storeRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "Frontend dependency path escapes the project."
        }
        $targetItem = Get-Item -LiteralPath $target -Force -ErrorAction Stop
        if (-not $targetItem.PSIsContainer -or ($targetItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Frontend dependency target must be an existing real package directory: $target"
        }
        $item = Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
        if ($null -ne $item) {
            if ($item.LinkType -ne 'Junction') { throw "Refusing to replace a non-junction: $link" }
            $currentTarget = [string](@($item.Target)[0])
            if ([string]::Equals($currentTarget.TrimEnd('\'), $target, [StringComparison]::OrdinalIgnoreCase)) { continue }
            # Delete only the directory junction itself; never recurse into its target.
            [IO.Directory]::Delete($link)
        }
        New-Item -ItemType Junction -Path $link -Target $target -ErrorAction Stop | Out-Null
    }
}
