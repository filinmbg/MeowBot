import wmi

try:
    w = wmi.WMI(namespace="root\\wmi")
    sensors = w.MSAcpi_ThermalZoneTemperature()

    if not sensors:
        print("Температура CPU недоступна на цій системі")
    else:
        for s in sensors:
            temp_c = (s.CurrentTemperature / 10.0) - 273.15
            print(f"{s.InstanceName}: {temp_c:.1f} °C")

except Exception as e:
    print("Не вдалося прочитати температуру через WMI:", e)
    print("Спробуй запустити PowerShell від імені адміністратора")