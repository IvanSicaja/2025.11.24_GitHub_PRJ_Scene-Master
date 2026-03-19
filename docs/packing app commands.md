1. Make sure you didn't rename or move used .venv folder. If you did, delete it and create new one because it is probably corrupted
   2. remove old .venv from available interpreters in pycharm
   3. configure you interpreted with that new .venv
   4. open your project treminal 
      5. press "x" on local
      6. make sure now stays e.g. "(.venv) PS C:\Users\Sicaja\Desktop\DB\06_DB-bussiness-and-bussiness-assets\2019.01.01_SmartCodeACADEMY_Official\2025.11.24_GitHub_PRJ_Scenify> 

2. if your used global .venv dont have, install pyInstaller simply with pycharm in your project or with pip
pip install pyinstaller
3. Create a standalone executable with icon
pyinstaller --onefile --windowed --name Scenify --icon=..\assets\media\icons\icon.ico --distpath publish --workpath publish\build --specpath publish main\main.py


4. Optional for removing non necessary files -> run in pycharm terminal
Get-ChildItem -Path .\publish -Recurse | Where-Object { $_.Extension -ne '.exe' } | Remove-Item -Force -Recurse