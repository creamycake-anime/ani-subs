# ani-subs

可用于 [Animeko](https://github.com/open-ani/animeko) 的数据源订阅。

## 用法

设置中添加订阅：

- `https://sub.creamycake.org/v1/css1.json`
- `https://sub.creamycake.org/v1/bt1.json`

支持 Ani 4.0.0 及以上版本。

## 维护数据源

每个 JSON 文件对应一个数据源。修改数据源时，直接编辑对应的单个 JSON 文件。

Web 数据源放在 `subs/web/t0/` 到 `subs/web/t4/`：

- `t0` 优先级最高，`t4` 优先级最低。
- 文件所在目录需要和 JSON 里的 `arguments.tier` 一致，例如 `arguments.tier` 为 `2` 的源放在 `subs/web/t2/`。
- 每个 Web 源都必须设置 `arguments.tier`。

JSON API 数据源放在 `subs/api/t0/` 到 `subs/api/t4/`，目录规则与 Web 数据源相同，和 Web 数据源一起生成到 `css1.json`。

BT 数据源放在 `subs/bt/`，例如 `subs/bt/AnimeGarden.json`。

新增数据源时，使用数据源名称作为文件名。如果名称重复，文件名使用 `2`、`3` 后缀区分，例如 `XX数据源.json` 和 `XX数据源2.json`。
调整 Web 数据源的 tier 时，同时修改 JSON 里的 `arguments.tier` 并把文件移动到对应的 `tN` 目录。

每个数据源都必须设置顶层的 `id`，并且在同一个订阅列表中不能重复。Animeko 6.3.0 起更新订阅时按 `id` 对应用户本地已有的数据源，因此修改名称、tier 或移动文件都不会丢失用户的启用状态等设置。

- `id` 一旦发布就不要再修改：修改后客户端会把它当作删除了旧数据源、新增了一个数据源，用户的设置随之丢失。
- 格式为小写字母和数字，用 `-` 连接。通常由名称转写，中文用拼音，例如 `稀饭动漫` 为 `xifan-dongman`；不要用域名，因为站点更换域名时 `id` 不能跟着变。

数据源质量如何评测（实播指标口径、广告评判、线路能力分级 T0–T6、报告格式）见 [docs/数据源评测规范.md](docs/数据源评测规范.md)。

### 客户端版本限制

依赖 Animeko 6.3.0 新特性的数据源（例如用了 `linkTemplate`）需要设置 `"clientVersions": ">=6.3.0"`，否则旧客户端会拿到一个用不了的源。需要给旧客户端保留旧配置时，另存一个文件并设置 `"clientVersions": "<6.3.0"`，`id` 和 `arguments.name` 与新配置相同。

### 生成整合的数据源订阅文件

编辑完成后，运行：

```shell
node scripts/build-subs.js
```

脚本会生成 `v1/css1.json` 和 `v1/bt1.json` 供本地检查。`v1/` 是生成目录，不提交到仓库。
如果已经生成过 `v1/`，也可以检查当前源文件是否和生成结果一致：

```shell
node scripts/build-subs.js --check
```
