$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

. (Join-Path $PSScriptRoot "P4RehabTTS.Common.ps1")

$form = New-Object System.Windows.Forms.Form
$form.Text = "P4 Rehab TTS"
$form.Size = New-Object System.Drawing.Size(720, 430)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$statusBox = New-Object System.Windows.Forms.TextBox
$statusBox.Multiline = $true
$statusBox.ReadOnly = $true
$statusBox.ScrollBars = "Vertical"
$statusBox.Font = New-Object System.Drawing.Font("Consolas", 10)
$statusBox.Location = New-Object System.Drawing.Point(16, 16)
$statusBox.Size = New-Object System.Drawing.Size(670, 300)
$form.Controls.Add($statusBox)

function Refresh-WindowStatus {
    try {
        $snapshot = Get-P4RehabTTSStatus
        $statusBox.Text = ($snapshot | Format-List | Out-String)
    }
    catch {
        $statusBox.Text = "Status error: $($_.Exception.Message)"
    }
}

function Invoke-UiAction([scriptblock]$Action) {
    $form.UseWaitCursor = $true
    try {
        & $Action
    }
    catch {
        [System.Windows.Forms.MessageBox]::Show(
            $_.Exception.Message,
            "P4 Rehab TTS",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    }
    finally {
        $form.UseWaitCursor = $false
        Refresh-WindowStatus
    }
}

$startButton = New-Object System.Windows.Forms.Button
$startButton.Text = "Start"
$startButton.Location = New-Object System.Drawing.Point(16, 332)
$startButton.Size = New-Object System.Drawing.Size(95, 34)
$startButton.Add_Click({ Invoke-UiAction { Start-P4RehabTTS } })
$form.Controls.Add($startButton)

$stopButton = New-Object System.Windows.Forms.Button
$stopButton.Text = "Stop"
$stopButton.Location = New-Object System.Drawing.Point(122, 332)
$stopButton.Size = New-Object System.Drawing.Size(95, 34)
$stopButton.Add_Click({ Invoke-UiAction { Stop-P4RehabTTS } })
$form.Controls.Add($stopButton)

$restartButton = New-Object System.Windows.Forms.Button
$restartButton.Text = "Restart"
$restartButton.Location = New-Object System.Drawing.Point(228, 332)
$restartButton.Size = New-Object System.Drawing.Size(95, 34)
$restartButton.Add_Click({
    Invoke-UiAction {
        Stop-P4RehabTTS
        Start-P4RehabTTS
    }
})
$form.Controls.Add($restartButton)

$refreshButton = New-Object System.Windows.Forms.Button
$refreshButton.Text = "Refresh"
$refreshButton.Location = New-Object System.Drawing.Point(334, 332)
$refreshButton.Size = New-Object System.Drawing.Size(95, 34)
$refreshButton.Add_Click({ Refresh-WindowStatus })
$form.Controls.Add($refreshButton)

$docsButton = New-Object System.Windows.Forms.Button
$docsButton.Text = "Open /docs"
$docsButton.Location = New-Object System.Drawing.Point(440, 332)
$docsButton.Size = New-Object System.Drawing.Size(110, 34)
$docsButton.Add_Click({
    try {
        Start-Process "http://127.0.0.1:8765/docs"
    }
    catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message) | Out-Null
    }
})
$form.Controls.Add($docsButton)

$logsButton = New-Object System.Windows.Forms.Button
$logsButton.Text = "Open logs"
$logsButton.Location = New-Object System.Drawing.Point(561, 332)
$logsButton.Size = New-Object System.Drawing.Size(125, 34)
$logsButton.Add_Click({
    Initialize-P4RehabTTSPaths
    Start-Process $script:LogRoot
})
$form.Controls.Add($logsButton)

Refresh-WindowStatus
[void]$form.ShowDialog()
