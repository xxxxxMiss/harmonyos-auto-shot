// NavDestination 路由 A
@Component
export struct NavRouteAPage {
  build() {
    NavDestination() {
      Column() {
        Text($r('app.string.nav_route_a'))
          .fontSize(24)
          .fontWeight(FontWeight.Bold)
      }
      .width('100%')
      .height('100%')
      .alignItems(HorizontalAlign.Center)
      .justifyContent(FlexAlign.Center)
    }
    .title('路由A')
  }
}

@Builder
export function NavRouteAPageBuilder() {
  NavRouteAPage()
}
