param([string]$Dir,[string]$Out)
$ErrorActionPreference='Continue'
$res = [System.ResolveEventHandler]{ param($s,$e) $n=($e.Name -split ',')[0]; if($n -like '*resources*'){ return $null }; $p=Join-Path $Dir ($n+'.dll'); if(Test-Path $p){ return [Reflection.Assembly]::LoadFrom($p)}; return $null }
[AppDomain]::CurrentDomain.add_AssemblyResolve($res)
$flags=[Reflection.BindingFlags]'Public,Instance,Static,DeclaredOnly'
$lines=New-Object System.Collections.Generic.List[string]
foreach($dll in 'RevitAPI.dll','RevitAPIUI.dll'){
  $a=[Reflection.Assembly]::LoadFrom((Join-Path $Dir $dll))
  $types=$null
  try{ $types=$a.GetExportedTypes() }catch [Reflection.ReflectionTypeLoadException]{ $types=$_.Exception.Types | ?{$_ -ne $null} }catch{ $types=@() }
  foreach($t in $types){
    $fn=$t.FullName; $lines.Add("T $fn")
    try{
      foreach($c in $t.GetConstructors()){ $lines.Add(("C {0}/{1}" -f $fn,$c.GetParameters().Count)) }
      $seen=@{}
      foreach($m in $t.GetMembers($flags)){
        if($m.MemberType -in 'Constructor','NestedType'){continue}
        $k="M $fn.$($m.Name)"; if(-not $seen.ContainsKey($k)){ $seen[$k]=1; $lines.Add($k) }
      }
    }catch{ $msg=$_.Exception.Message; $lines.Add("E $fn $($msg.Substring(0,[Math]::Min(60,$msg.Length)))") }
  }
}
[IO.File]::WriteAllLines($Out,$lines,(New-Object Text.UTF8Encoding($false)))
"$Out : $($lines.Count) lines"
