# Java CPU 飙高 — GC 线程频繁运行排查

## 现象
- 系统负载飙高（如远超 CPU 核心数 × 0.7）
- `top` 命令看到 Java 进程 CPU 占用高
- 使用 `jstack <pid>` 导出线程堆栈，发现大量 GC 线程

## 排查步骤

### 1. 定位进程
```bash
top -c
# 按 P 按 CPU 排序，找到 Java 进程 PID
```

### 2. 查看 GC 状态
```bash
# 打印 GC 概况
jstat -gcutil <pid> <间隔毫秒> <次数>
# 示例：每秒打印一次，共 5 次
jstat -gcutil 12345 1000 5

# 输出关键字段：
#   S0/S1 — Survivor 区使用率
#   E     — Eden 区使用率
#   O     — Old 区使用率（老年代）
#   M     — Metaspace 使用率
#   YGC   — Young GC 次数
#   YGCT  — Young GC 累计时间
#   FGC   — Full GC 次数
#   FGCT  — Full GC 累计时间
```

### 3. 导出线程堆栈
```bash
jstack -l <pid> > jstack_dump.txt
# 查找 GC 线程：
grep -i "gc\|GC\|G1\|Concurrent" jstack_dump.txt
```

### 4. 常见原因

| 原因 | 特征 | 对策 |
|------|------|------|
| **堆内存太小** | FGC 频繁，O 区迅速填满 | 增大 -Xmx（如 -Xmx4g） |
| **内存泄漏** | 堆持续增长，FGC 无法回收 | 用 jmap dump heap，用 MAT/Eclipse 分析 |
| **GC 算法不当** | 吞吐量低，STW 时间长 | 改用 G1（-XX:+UseG1GC）或 ZGC |
| **大对象分配** | G1 Humongous 区域频繁 | 检查代码中一次性分配的大数组/集合 |

### 5. 堆转储分析
```bash
# 生成堆转储（会暂停 JVM，生产需谨慎）
jmap -dump:live,format=b,file=heap.hprof <pid>

# 或自动宕机时生成（预配置）
# -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/path/
```

## 预防措施
- 配置 JVM 参数：`-Xms` 和 `-Xmx` 设到合理值（通常为系统内存的 50-70%）
- 使用 G1GC 或 ZGC（低延迟场景）
- 添加 GC 日志：`-Xlog:gc*:file=gc.log:time,level,tags`
- 配合 Prometheus + Grafana 监控堆内存和 GC 频率

## 参考
- [JVM GC 调优官方文档](https://docs.oracle.com/en/java/javase/17/gctuning/)
