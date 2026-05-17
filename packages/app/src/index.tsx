import { render } from "solid-js/web"
import { Router } from "@solidjs/router"
import App from "./App"
import "./index.css"

const root = document.getElementById("root")
if (root) render(() => (
  <Router root={App}>
    {/* 路由在主 App 组件中定义 */}
  </Router>
), root)
