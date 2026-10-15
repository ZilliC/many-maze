// Ventanas en pantalla de una aplicación (por defecto mANY-MAZE y Python): id, tamaño y título.
//   swift qa/herramientas/ventanas.swift [nombre…]          → una línea por ventana: id<TAB>ancho×alto<TAB>dueño<TAB>título
//   swift qa/herramientas/ventanas.swift --esperar 30 [nombre…]  → espera hasta 30 s a que aparezca la primera ventana
//                                                              e imprime los segundos que tardó (B00-01)
// La usan captura.sh (capturar solo la ventana de la app, aunque se salga de la pantalla) y B00-01.
import CoreGraphics
import Foundation

var args = Array(CommandLine.arguments.dropFirst())
var wait: Double = 0
if args.first == "--esperar", args.count > 1 { wait = Double(args[1]) ?? 30; args.removeFirst(2) }
let owners = Set((args.isEmpty ? ["mANY-MAZE", "Python"] : args).map { $0.lowercased() })

func windows() -> [[String: Any]] {
    let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
        as? [[String: Any]] ?? []
    return list.filter { w in
        let owner = (w[kCGWindowOwnerName as String] as? String ?? "").lowercased()
        let layer = w[kCGWindowLayer as String] as? Int ?? 1
        return owners.contains(owner) && layer == 0
    }
}

if wait > 0 {
    let t0 = Date()
    while Date().timeIntervalSince(t0) < wait {
        if !windows().isEmpty { print(String(format: "%.1f", Date().timeIntervalSince(t0))); exit(0) }
        usleep(100_000)
    }
    print("sin ventana tras \(Int(wait)) s"); exit(1)
}
for w in windows() {
    let id = w[kCGWindowNumber as String] as? Int ?? 0
    let b = w[kCGWindowBounds as String] as? [String: Double] ?? [:]
    let owner = w[kCGWindowOwnerName as String] as? String ?? ""
    let title = w[kCGWindowName as String] as? String ?? ""
    print("\(id)\t\(Int(b["Width"] ?? 0))×\(Int(b["Height"] ?? 0))\t\(owner)\t\(title)")
}
