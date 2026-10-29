import AppKit
import Foundation
import Darwin

// JSONはこの境界だけで解釈し、全件保護を不変条件とする。
struct Inventory: Decodable {
    struct Totals: Decodable {
        let process_count: Int
        let rss_bytes: Int64
        let cpu_percent: Double
        let cpu_complete: Bool
    }
    struct Row: Decodable {
        let pid: Int
        let created_at: Double
        let parent_pid: Int?
        let name: String
        let rss_bytes: Int64
        let cpu_percent: Double?
        let usage_state: String
        let attribution: String
        let thread_ids: [String]
    }
    struct Group: Decodable {
        let key: String
        let title: String
    }
    let schema_version: String
    let read_only: Bool
    let sample_epoch: Double
    let owner: String
    let owner_found: Bool
    let collection_complete: Bool
    let processes: [Row]
    let totals: Totals
    let chat_groups: [Group]
}
struct Snapshot: Decodable {
    struct Assessment: Decodable {
        let pid: Int
        let created_at: Double
        let status: String
        let reason: String
    }
    struct Pressure: Decodable {
        let status: String
        let memory_pressure_level: Int?
        let swap_used_bytes: Int64?
    }
    let schema: String
    let schema_version: Int
    let overall: String
    let inventory: Inventory?
    let assessment: [Assessment]
    let pressure: Pressure
    let next_interval_seconds: Int
    let automatic_cleanup_supported: Bool
    let automatic_cleanup_reason: String
    // 登録機構へ接続するまでは0のみ。inventoryから所有権を推測しない。
    let managed_connections_registered: Int?

    var managedCleanupAvailable: Bool {
        automatic_cleanup_supported && automatic_cleanup_reason == "managed-connection-end-only"
    }
    let error_code: String?

    static func decode(_ data: Data) throws -> Snapshot {
        guard data.count <= 4 * 1024 * 1024 else { throw AppError.invalid }
        let value = try JSONDecoder().decode(Snapshot.self, from: data)
        guard value.schema == "runtime_process_guard.resident.v1", value.schema_version == 1,
              ["ok", "unknown"].contains(value.overall),
              ["normal", "pressured", "unknown"].contains(value.pressure.status),
              [5, 15, 30, 60].contains(value.next_interval_seconds),
              ((!value.automatic_cleanup_supported && value.automatic_cleanup_reason == "active-request-tracking-unavailable")
               || value.managedCleanupAvailable),
              (value.managed_connections_registered ?? 0) == 0,
              value.pressure.swap_used_bytes.map({ $0 >= 0 }) ?? true else { throw AppError.invalid }
        if let inventory = value.inventory {
            guard inventory.schema_version == "runtime-process-guard/inventory-v2", inventory.read_only,
                  inventory.owner == "ChatGPT", inventory.sample_epoch.isFinite,
                  inventory.sample_epoch > 0,
                  inventory.sample_epoch <= Date().timeIntervalSince1970 + 5,
                  inventory.totals.process_count == inventory.processes.count, inventory.processes.count <= 10000,
                  inventory.totals.rss_bytes >= 0, inventory.totals.cpu_percent.isFinite,
                  inventory.totals.cpu_percent >= 0 else { throw AppError.invalid }
            var identities = Set<String>()
            var pids = Set<Int>()
            var total: Int64 = 0
            for row in inventory.processes {
                guard row.pid > 0, row.created_at.isFinite, row.created_at > 0,
                      row.created_at <= inventory.sample_epoch + 1,
                      row.rss_bytes >= 0, row.name.count <= 512,
                      row.cpu_percent.map({ $0.isFinite && $0 >= 0 }) ?? true,
                      ["unknown", "active", "idle"].contains(row.usage_state),
                      ["shared", "attributed", "unknown"].contains(row.attribution),
                      pids.insert(row.pid).inserted else { throw AppError.invalid }
                let result = total.addingReportingOverflow(row.rss_bytes)
                guard !result.overflow else { throw AppError.invalid }
                total = result.partialValue
                identities.insert("\(row.pid):\(row.created_at)")
            }
            guard total == inventory.totals.rss_bytes,
                  value.assessment.count == inventory.processes.count else { throw AppError.invalid }
            var assessed = Set<String>()
            for item in value.assessment {
                let key = "\(item.pid):\(item.created_at)"
                guard item.status == "protected", item.created_at.isFinite,
                      ["unknown-usage", "shared", "active", "protected-process", "unowned"].contains(item.reason),
                      identities.contains(key), assessed.insert(key).inserted else { throw AppError.invalid }
            }
            let parents = Dictionary(uniqueKeysWithValues: inventory.processes.map { ($0.pid, $0.parent_pid) })
            for row in inventory.processes {
                guard row.parent_pid != row.pid,
                      row.parent_pid.map({ pids.contains($0) }) ?? true else { throw AppError.invalid }
                var visited: Set<Int> = [row.pid]
                var current = row.parent_pid
                while let pid = current {
                    guard visited.insert(pid).inserted else { throw AppError.invalid }
                    current = parents[pid] ?? nil
                }
            }
            if value.overall == "ok" {
                guard inventory.collection_complete, inventory.owner_found,
                      value.error_code == nil, value.pressure.status != "unknown" else { throw AppError.invalid }
            }
        } else {
            guard value.overall == "unknown", value.assessment.isEmpty else { throw AppError.invalid }
        }
        return value
    }
}

enum AppError: Error { case arguments, invalid, locked, launch, timeout, outputLimit }
struct Options {
    var cli = ""
    var state = ""
    var threads: String?
    var bindings: String?
    var smoke: Double?
    var noUI = false
    var fixture: String?
    static func parse() throws -> Options {
        var result = Options()
        if let config = Bundle.main.url(forResource: "ResidentDefaults", withExtension: "plist"),
           let data = try? Data(contentsOf: config),
           let defaults = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: String] {
            result.cli = defaults["cli"] ?? ""
            result.state = defaults["state"] ?? ""
            result.threads = defaults["threads_db"]
            result.bindings = defaults["bindings"]
        }
        var args = Array(CommandLine.arguments.dropFirst())
        while !args.isEmpty {
            let key = args.removeFirst()
            if key == "--no-ui" { result.noUI = true; continue }
            guard !args.isEmpty else { throw AppError.arguments }
            let value = args.removeFirst()
            switch key {
            case "--cli": result.cli = value
            case "--state": result.state = value
            case "--threads-db": result.threads = value
            case "--bindings": result.bindings = value
            case "--smoke-seconds":
                guard let seconds = Double(value), seconds.isFinite, (1...300).contains(seconds) else { throw AppError.arguments }
                result.smoke = seconds
            case "--validate-fixture": result.fixture = value
            default: throw AppError.arguments
            }
        }
        if result.fixture != nil { return result }
        guard result.cli.hasPrefix("/"), result.state.hasPrefix("/"),
              FileManager.default.isExecutableFile(atPath: result.cli),
              result.threads.map({ $0.hasPrefix("/") }) ?? true,
              result.bindings.map({ $0.hasPrefix("/") }) ?? true,
              !result.noUI || result.smoke != nil else { throw AppError.arguments }
        return result
    }
}

// アプリ専用stateの隣に単一起動lock。既存inodeをunlinkしない。
final class InstanceLock {
    private var descriptor: Int32 = -1
    init(state: String) throws {
        let parent = URL(fileURLWithPath: state).deletingLastPathComponent()
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        descriptor = Darwin.open(state + ".app-lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard descriptor >= 0 else { throw AppError.locked }
        var metadata = stat()
        guard fstat(descriptor, &metadata) == 0, metadata.st_uid == getuid(),
              metadata.st_mode & S_IFMT == S_IFREG, metadata.st_mode & 0o077 == 0,
              flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            Darwin.close(descriptor); descriptor = -1; throw AppError.locked
        }
    }
    deinit { if descriptor >= 0 { Darwin.close(descriptor) } }
}

struct CollectionResult {
    let data: Data?
    let elapsed: Double
    let failure: String?
    let childRunning: Bool
}
final class Collector {
    // このクラスがProcess.runで作ったcollectorにだけSIGTERMを送る。
    private let guardLock = NSLock()
    private var child: Process?
    func terminateOwnedCollector() {
        guardLock.lock(); defer { guardLock.unlock() }
        if let current = child, current.isRunning { current.terminate() }
    }
    func run(options: Options) -> CollectionResult {
        let started = ProcessInfo.processInfo.systemUptime
        let process = Process()
        let output = Pipe(), errors = Pipe()
        process.executableURL = URL(fileURLWithPath: options.cli)
        var args = ["resident", "snapshot", "--owner", "ChatGPT", "--state", options.state, "--json"]
        if let path = options.threads { args += ["--threads-db", path] }
        if let path = options.bindings { args += ["--bindings", path] }
        process.arguments = args
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = output
        process.standardError = errors
        do { try process.run() } catch {
            return CollectionResult(data: nil, elapsed: 0, failure: "collector起動失敗", childRunning: false)
        }
        guardLock.lock(); child = process; guardLock.unlock()
        let outFD = output.fileHandleForReading.fileDescriptor
        let errFD = errors.fileHandleForReading.fileDescriptor
        _ = fcntl(outFD, F_SETFL, O_NONBLOCK)
        _ = fcntl(errFD, F_SETFL, O_NONBLOCK)
        var bytes = Data(), total = 0
        var failure: String?
        var terminatedAt: Double?
        var buffer = [UInt8](repeating: 0, count: 16384)
        func drain(_ fd: Int32, save: Bool) {
            while true {
                let count = Darwin.read(fd, &buffer, buffer.count)
                if count <= 0 { break }
                total += count
                if total > 4 * 1024 * 1024 { failure = "collector出力上限超過"; break }
                if save { bytes.append(contentsOf: buffer.prefix(count)) }
            }
        }
        repeat {
            drain(outFD, save: true); drain(errFD, save: false)
            let now = ProcessInfo.processInfo.systemUptime
            if now - started >= 10 && failure == nil { failure = "collector時間超過" }
            if failure != nil && terminatedAt == nil {
                if process.isRunning { process.terminate() }
                terminatedAt = now
            }
            if let ended = terminatedAt, now - ended > 2 && process.isRunning { break }
            if process.isRunning { Thread.sleep(forTimeInterval: 0.02) }
        } while process.isRunning
        drain(outFD, save: true); drain(errFD, save: false)
        let running = process.isRunning
        if !running {
            process.waitUntilExit()
            guardLock.lock(); child = nil; guardLock.unlock()
        }
        try? output.fileHandleForReading.close(); try? errors.fileHandleForReading.close()
        if !running && ![0, 40].contains(process.terminationStatus) && failure == nil { failure = "collector観測失敗" }
        return CollectionResult(data: failure == nil ? bytes : nil,
                                elapsed: ProcessInfo.processInfo.systemUptime - started,
                                failure: failure, childRunning: running)
    }
}

final class Monitor: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let options: Options
    let instanceLock: InstanceLock
    let collector = Collector()
    var statusItem: NSStatusItem?
    var menu: NSMenu?
    var snapshot: Snapshot?
    var errorMessage: String?
    var timer: Timer?
    var sleeping = false
    var resetCPUBaseline = false
    var showCPUUnmeasured = false
    var collecting = false
    var observationGeneration = 0
    var stopping = false
    var successes = 0
    var failures = 0
    var partialObservations = 0
    var active = 0
    var maximumParallel = 0
    var runs = 0
    var totalCollectorWall = 0.0
    var maximumCollectorWall = 0.0
    let started = ProcessInfo.processInfo.systemUptime
    var observers: [NSObjectProtocol] = []

    init(options: Options) throws {
        self.options = options
        self.instanceLock = try InstanceLock(state: options.state)
        super.init()
    }
    func applicationDidFinishLaunching(_ notification: Notification) { start() }
    func start() {
        if !options.noUI {
            let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
            statusItem = item
            item.button?.title = "監視 …"
            let value = NSMenu(); value.delegate = self; menu = value; item.menu = value
            rebuildMenu()
        }
        let center = NSWorkspace.shared.notificationCenter
        observers.append(center.addObserver(forName: NSWorkspace.willSleepNotification, object: nil, queue: .main) { [weak self] _ in
            self?.sleeping = true; self?.observationGeneration += 1; self?.timer?.invalidate(); self?.snapshot = nil; self?.updateStatus()
        })
        observers.append(center.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
            self?.sleeping = false; self?.observationGeneration += 1; self?.snapshot = nil; self?.resetCPUBaseline = true; self?.refresh()
        })
        refresh()
        if let seconds = options.smoke {
            DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { [weak self] in self?.finish() }
        }
    }
    @objc func refresh() {
        guard !sleeping, !collecting, !stopping else { return }
        timer?.invalidate()
        collecting = true; active += 1; runs += 1; maximumParallel = max(maximumParallel, active)
        updateStatus()
        let generation = observationGeneration
        DispatchQueue.global(qos: .utility).async { [self] in
            let result = collector.run(options: options)
            let decoded = result.data.flatMap { try? Snapshot.decode($0) }
            DispatchQueue.main.async { [self] in accept(result, decoded: decoded, generation: generation) }
        }
    }
    func accept(_ result: CollectionResult, decoded: Snapshot?, generation: Int) {
        active -= 1
        collecting = result.childRunning
        totalCollectorWall += result.elapsed
        maximumCollectorWall = max(maximumCollectorWall, result.elapsed)
        if generation != observationGeneration {
            snapshot = nil
            if stopping { outputSmokeAndExit() }
            else if !sleeping && !collecting { refresh() }
            return
        }
        if let decoded {
            snapshot = decoded
            errorMessage = decoded.overall == "ok" ? nil : "観測不完全・全件保護"
            if decoded.overall == "ok" { successes += 1 }
            else if decoded.inventory != nil { partialObservations += 1 }
            else { failures += 1 }
            showCPUUnmeasured = resetCPUBaseline; resetCPUBaseline = false
        } else {
            snapshot = nil; errorMessage = result.failure ?? "観測不完全または形式不正"; failures += 1
        }
        updateStatus()
        if stopping { outputSmokeAndExit(); return }
        if !sleeping && !collecting {
            let seconds = snapshot?.next_interval_seconds ?? 60
            timer = Timer.scheduledTimer(withTimeInterval: Double(seconds), repeats: false) { [weak self] _ in self?.refresh() }
        }
    }
    func age() -> Double? {
        guard let inventory = snapshot?.inventory else { return nil }
        return max(0, Date().timeIntervalSince1970 - inventory.sample_epoch)
    }
    func updateStatus() {
        guard let button = statusItem?.button else { return }
        if sleeping { button.title = "監視 休止" }
        else if let inventory = snapshot?.inventory, let seconds = age(), seconds <= 90 {
            button.title = "Codex \(inventory.totals.process_count)件\(snapshot?.overall == "unknown" ? " △" : "")"
        } else { button.title = collecting ? "監視 更新中" : "監視 未確認" }
        button.toolTip = snapshot?.managedCleanupAvailable == true
            ? "Codex配下のみ観測・未管理プロセスは保護・自動整理は管理付き接続の終了時のみ"
            : "Codex配下のみ観測・未管理プロセスは保護・要求追跡の対応を未確認"
    }
    func menuWillOpen(_ menu: NSMenu) { updateStatus(); rebuildMenu() }
    func add(_ title: String, to target: NSMenu? = nil, indent: Int = 0) {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.indentationLevel = min(indent, 8)
        (target ?? menu)?.addItem(item)
    }
    func clean(_ value: String) -> String {
        String(value.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) }.prefix(100))
    }
    func rebuildMenu() {
        guard let menu else { return }
        menu.removeAllItems()
        add("Codex配下のプロセス観測")
        if let snapshot, let inventory = snapshot.inventory, let seconds = age() {
            add("観測から\(Int(seconds))秒\(seconds > 90 ? "・古い観測" : "")")
            if snapshot.overall == "unknown" { add("観測不完全・取得できた範囲を表示") }
            add("\(inventory.totals.process_count)件・RSS算術和 \(String(format: "%.1f", Double(inventory.totals.rss_bytes) / 1048576)) MiB")
            add(inventory.totals.cpu_complete && !showCPUUnmeasured ? "CPU差分 \(String(format: "%.1f", inventory.totals.cpu_percent))%" : "CPU 初回・部分値は未測定")
            add("保護 \(inventory.processes.count)件・停止候補 0件")
            add("負荷状態: \(["normal": "通常", "pressured": "上昇", "unknown": "未確認"][snapshot.pressure.status] ?? "未確認")")
            let tree = NSMenu()
            let titles = Dictionary(inventory.chat_groups.map { ($0.key, clean($0.title)) }, uniquingKeysWith: { first, _ in first })
            let byPID = Dictionary(uniqueKeysWithValues: inventory.processes.map { ($0.pid, $0) })
            let reasons = Dictionary(uniqueKeysWithValues: snapshot.assessment.map { ($0.pid, $0.reason) })
            let reasonLabels = ["unknown-usage": "使用不明", "shared": "共有", "active": "処理中", "protected-process": "保護対象", "unowned": "所有未確認"]
            var children: [Int: [Inventory.Row]] = [:]
            var roots: [Inventory.Row] = []
            for row in inventory.processes {
                if let parent = row.parent_pid { children[parent, default: []].append(row) }
                else { roots.append(row) }
            }
            var count = 0
            func append(_ row: Inventory.Row, depth: Int) {
                guard count < 80 else { return }
                count += 1
                let names = row.thread_ids.compactMap { titles[$0] }.prefix(2).joined(separator: "・")
                let label = names.isEmpty ? (row.attribution == "shared" ? "共有" : "未特定") : names
                add("PID \(row.pid) \(clean(row.name)) · \(label) [保護: \(reasonLabels[reasons[row.pid] ?? ""] ?? "未確認")]", to: tree, indent: depth)
                for child in (children[row.pid] ?? []).sorted(by: { $0.pid < $1.pid }) { append(child, depth: depth + 1) }
            }
            for row in roots.sorted(by: { $0.pid < $1.pid }) { append(row, depth: 0) }
            if byPID.count > count { add("ほか\(byPID.count - count)件（表示上限80件）", to: tree) }
            let item = NSMenuItem(title: "親子ツリー・PID・登録名", action: nil, keyEquivalent: "")
            item.submenu = tree; menu.addItem(item)
        } else {
            add(sleeping ? "休止中・復帰後に再観測" : (errorMessage ?? "観測待ち"))
            add("件数・RSS・CPU: 未確認")
            add("全件保護・停止候補 0件")
        }
        add(snapshot?.managedCleanupAvailable == true
            ? "自動整理: 管理付き接続の終了時のみ"
            : "自動整理: 未対応（要求追跡の対応を未確認）")
        add("未管理プロセスは保護")
        add("管理付き接続 0件・待機（アプリへの接続登録なし）")
        add("RSSは共有メモリを含む算術和")
        menu.addItem(.separator())
        let refresh = NSMenuItem(title: collecting ? "更新中" : "今すぐ更新", action: #selector(refresh), keyEquivalent: "r")
        refresh.target = self; refresh.isEnabled = !collecting && !sleeping; menu.addItem(refresh)
        let quit = NSMenuItem(title: "終了", action: #selector(quit), keyEquivalent: "q")
        quit.target = self; menu.addItem(quit)
    }
    @objc func quit() { finish() }
    func finish() {
        guard !stopping else { return }
        stopping = true; timer?.invalidate()
        if collecting {
            collector.terminateOwnedCollector()
            DispatchQueue.main.asyncAfter(deadline: .now() + 13) { [weak self] in self?.outputSmokeAndExit() }
        } else { outputSmokeAndExit() }
    }
    func outputSmokeAndExit() {
        if options.smoke != nil {
            var usage = rusage(); getrusage(RUSAGE_SELF, &usage)
            let cpu = Double(usage.ru_utime.tv_sec + usage.ru_stime.tv_sec) + Double(usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) / 1000000
            var childUsage = rusage(); getrusage(RUSAGE_CHILDREN, &childUsage)
            let childCPU = Double(childUsage.ru_utime.tv_sec + childUsage.ru_stime.tv_sec) + Double(childUsage.ru_utime.tv_usec + childUsage.ru_stime.tv_usec) / 1000000
            let receipt: [String: Any] = ["schema": "runtime_process_guard.menubar-smoke.v1", "collector_runs": runs,
                "collector_successes": successes, "collector_failures": failures, "collector_partial_observations": partialObservations,
                "maximum_parallel_collectors": maximumParallel, "collector_running": collecting,
                "elapsed_seconds": ProcessInfo.processInfo.systemUptime - started,
                "app_cpu_seconds": cpu, "app_peak_rss_bytes": usage.ru_maxrss,
                "reaped_children_cpu_seconds": childCPU, "monitor_total_cpu_seconds": cpu + childCPU,
                "collector_total_wall_seconds": totalCollectorWall, "collector_max_wall_seconds": maximumCollectorWall,
                "automatic_cleanup_supported": snapshot?.managedCleanupAvailable ?? false,
                "managed_connections_registered": 0]
            if let data = try? JSONSerialization.data(withJSONObject: receipt, options: [.sortedKeys]), let text = String(data: data, encoding: .utf8) { print(text) }
        }
        exit(failures == 0 && partialObservations == 0 && !collecting ? 0 : 40)
    }
}

if CommandLine.arguments.contains("--help") {
    print("利用方法: RuntimeProcessMenuBar --cli ABS --state ABS [--threads-db ABS] [--bindings ABS] [--smoke-seconds 1...300] [--no-ui]\n検証: --validate-fixture ABS\nCodex配下のみ・全件保護。自動整理は対応guardで管理付き接続の終了時のみ。未管理プロセスは保護。接続登録0件・待機。自動起動登録は行いません。")
    exit(0)
}
do {
    let options = try Options.parse()
    if let fixture = options.fixture {
        let data = try Data(contentsOf: URL(fileURLWithPath: fixture), options: .mappedIfSafe)
        _ = try Snapshot.decode(data)
        print("fixture形式検証成功")
        exit(0)
    }
    let monitor = try Monitor(options: options)
    if options.noUI {
        monitor.start()
        RunLoop.main.run()
    } else {
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)
        application.delegate = monitor
        application.run()
    }
} catch {
    fputs("起動または形式検証に失敗。引数・CLI実体・単一起動・入力形式を確認してください。\n", stderr)
    exit(40)
}
