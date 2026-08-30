// NavDestination 路由 B
@Component
export struct NavRouteBPage {
  build() {
    NavDestination() {
      Column() {
        Text($r('app.string.nav_route_b'))
          .fontSize(24)
          .fontWeight(FontWeight.Bold)
      }
      .width('100%')
      .height('100%')
      .alignItems(HorizontalAlign.Center)
      .justifyContent(FlexAlign.Center)
    }
    .title('路由B')
  }
}

@Builder
export function NavRouteBPageBuilder() {
  NavRouteBPage()
}
