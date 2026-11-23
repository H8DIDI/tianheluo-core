param([string]$Dir,[string]$Out)
Add-Type -AssemblyName System.Reflection.Metadata
$lines=New-Object System.Collections.Generic.List[string]
foreach($dll in 'RevitAPI.dll','RevitAPIUI.dll'){
  $fs=[IO.File]::OpenRead((Join-Path $Dir $dll)); $pe=New-Object System.Reflection.PortableExecutable.PEReader($fs)
  $md=[System.Reflection.Metadata.PEReaderExtensions]::GetMetadataReader($pe)
  foreach($th in $md.TypeDefinitions){
    $td=$md.GetTypeDefinition($th)
    $vis=$td.Attributes -band 7
    if($vis -ne 1){ continue }   # public top-level only (nested handled below as skipped)
    $ns=$md.GetString($td.Namespace); $nm=$md.GetString($td.Name); $fn= if($ns){"$ns.$nm"}else{$nm}
    $lines.Add("T $fn")
    $seen=@{}
    foreach($mh in $td.GetMethods()){
      $m=$md.GetMethodDefinition($mh); $mn=$md.GetString($m.Name)
      $acc=$m.Attributes -band 7
      if($acc -lt 4 ){ continue }   # public=6, family=4, famorassem=5 -> keep >=4 (protected shows to subclassers)
      if($mn -eq '.ctor'){ $n=0; foreach($ph in $m.GetParameters()){ if($md.GetParameter($ph).SequenceNumber -gt 0){$n++} }; $lines.Add("C $fn/$n"); continue }
      if($mn -eq '.cctor'){ continue }
      $k="M $fn.$mn"; if(-not $seen.ContainsKey($k)){ $seen[$k]=1; $lines.Add($k) }
    }
    foreach($ph in $td.GetProperties()){ $mn=$md.GetString($md.GetPropertyDefinition($ph).Name); $k="M $fn.$mn"; if(-not $seen.ContainsKey($k)){ $seen[$k]=1; $lines.Add($k) } }
    foreach($fh in $td.GetFields()){ $f=$md.GetFieldDefinition($fh); if(($f.Attributes -band 7) -ge 4){ $mn=$md.GetString($f.Name); if($mn -eq 'value__'){continue}; $k="M $fn.$mn"; if(-not $seen.ContainsKey($k)){ $seen[$k]=1; $lines.Add($k) } } }
    foreach($eh in $td.GetEvents()){ $mn=$md.GetString($md.GetEventDefinition($eh).Name); $k="M $fn.$mn"; if(-not $seen.ContainsKey($k)){ $seen[$k]=1; $lines.Add($k) } }
  }
  $pe.Dispose(); $fs.Dispose()
}
[IO.File]::WriteAllLines($Out,$lines,(New-Object Text.UTF8Encoding($false)))
"$Out : $($lines.Count) lines"
